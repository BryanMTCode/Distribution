"""Infraestructura de pruebas.

Las pruebas corren contra un PostgreSQL **real**, no contra SQLite ni mocks: la
mitad del diseño vive en constraints, triggers y columnas generadas que solo
existen en PostgreSQL. Una suite que no los ejerce no prueba este sistema.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# El puerto estándar de PostgreSQL. Si corres la base en otro, exporta
# DSD_TEST_DATABASE_URL en vez de editar esto.
URL_PRUEBAS = os.environ.get(
    "DSD_TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:dsd@127.0.0.1:5432/dsd_test",
)

# Datos transaccionales: se vacían entre pruebas. CASCADE arrastra a las
# tablas que dependen de ellas, así que el orden no importa.
#
# Los datos de REFERENCIA (roles, permisos, unidades, canales, motivos, la
# lista de precios por defecto) NO están aquí: los siembra la migración 0009 y
# el código depende de sus códigos literales. Vaciarlos entre pruebas sería
# probar contra un sistema que no existe en producción.


def texto_plano(respuesta) -> str:
    """El HTML con los espacios colapsados, para afirmar frases completas.

    Una plantilla parte las frases donde se le acaba el renglón, así que
    `"sin bodega no hay de dónde cargar" in respuesta.text` falla por un salto de
    línea que no le importa a nadie. Costó dos pruebas rojas sobre código correcto;
    con esto el assert dice lo que quiere decir.
    """
    return " ".join(respuesta.text.split())


def solo_texto(respuesta) -> str:
    """Lo que la persona REALMENTE lee: sin etiquetas y con los espacios colapsados.

    `texto_plano` colapsa los espacios pero deja el HTML, así que afirmar una frase
    que la plantilla parte con un `<strong>` —"De 30 unidades mermadas, **24** salen
    de la bolsa"— falla por una etiqueta que nadie ve. La alternativa era escribir
    los `<strong>` dentro del assert, y entonces la prueba se rompe cada vez que
    alguien cambia la tipografía de una cifra, que es justo lo que no debería
    importarle.

    También quita el `<style>` y el `<script>`: su contenido no es texto que alguien
    lea, y una regla de CSS que mencione una palabra haría pasar un assert por
    accidente.
    """
    crudo = re.sub(
        r"<(style|script)\b[^>]*>.*?</\1>", " ", respuesta.text, flags=re.S | re.I
    )
    return " ".join(re.sub(r"<[^>]+>", " ", crudo).split())


TABLAS_VOLATILES = [
    # El piso de retención del change_log (migración 0034). Es estado de la
    # INSTALACIÓN, no dato de referencia: una prueba que lo sube —la del job de
    # poda— dejaría a todas las que corran después recibiendo «resincroniza» en
    # cada pull. Pasó, y el síntoma fue una prueba de ruta fallando por un motivo
    # que no tenía nada que ver con rutas.
    "sync_retencion",
    # Las entradas de mercancía: cuelgan de `almacenes` y de `usuarios`, así que
    # el CASCADE las alcanzaría, pero van explícitas por la misma razón que las
    # del piloto — leer esta lista no debería exigir seguir llaves foráneas en la
    # cabeza.
    # Compras: `pagos_proveedor` cuelga de `cuentas_por_pagar`, que cuelga de
    # `entradas`, que cuelga de `almacenes`. El CASCADE llegaría, y van
    # explícitas por lo mismo que las demás.
    #
    # `producto_costos` también: cuelga de `productos`, y un costo que sobreviva
    # entre pruebas haría que la de "la primera compra fija el promedio" midiera
    # una segunda compra.
    "pagos_proveedor",
    "cuentas_por_pagar",
    "producto_costos",
    "proveedores",
    "salida_detalle",
    "salidas",
    "entrada_detalle",
    "entradas",
    # Las tablas del piloto (Fase 3) van explícitas aunque el CASCADE de
    # `usuarios` las alcanzaría: en la Fase 7 ya se pagó el precio de confiar en
    # eso, y leer la lista no debería exigir seguir llaves foráneas en la cabeza.
    #
    # `piloto_criterios` NO está aquí, y es la distinción que importa: son datos
    # de REFERENCIA sembrados por la migración 0024, como los motivos de merma.
    # Vaciarlos entre pruebas dejaría el piloto sin criterios —la pantalla diría
    # que todo cumple— y además probaría contra un sistema que no existe.
    "piloto_incidencias",
    "piloto_jornadas",
    "pilotos",
    # Los modelos de lectura del tablero (Fase 7) van PRIMERO y explícitamente.
    #
    # `tablero_refrescos` y `tablero_cartera` no tienen llave foránea a nada, así
    # que el CASCADE de las otras tablas no se los lleva: sin esto, el renglón que
    # dejó una prueba sobrevive a la siguiente, y la prueba de "el tablero nunca
    # se ha calculado" ve la hora de la corrida anterior. Es el mismo defecto que
    # las cachés globales de Streamlit en la Fase 8, con otra cara.
    "tablero_refrescos",
    "tablero_cartera",
    "tablero_mes_ruta",
    "tablero_dia",
    "objetivos_ruta",
    "sesiones",
    "folios_rangos",
    "jobs",
    "auditoria",
    "usuarios_rutas",
    "usuarios_permisos",
    "dispositivos",
    "clientes",
    "productos",
    "categorias",
    "marcas",
    "rutas",
    "usuarios",
    "almacenes",
    "sucursales",
]


def _preparar_esquema() -> None:
    base = URL_PRUEBAS.rsplit("/", 1)[0] + "/postgres"
    sync = base.replace("+psycopg", "")
    nombre = URL_PRUEBAS.rsplit("/", 1)[1]
    import psycopg

    with psycopg.connect(sync, autocommit=True) as con:
        existe = con.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (nombre,)
        ).fetchone()
        if not existe:
            con.execute(f'CREATE DATABASE "{nombre}"')
    with psycopg.connect(URL_PRUEBAS.replace("+psycopg", ""), autocommit=True) as con:
        con.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # `upgrade head` SIEMPRE, no solo cuando la base está vacía.
    #
    # Antes esto se saltaba si ya existía `alembic_version`, y el resultado era
    # una trampa: en CI la base nace limpia y todo pasa, pero en la máquina de
    # quien ya corrió la suite una vez, una migración nueva NO se aplicaba y las
    # pruebas de sus constraints fallaban con "DID NOT RAISE" — un mensaje que
    # no apunta a ningún lado. Alembic es idempotente y cuando no hay nada
    # pendiente tarda menos de un segundo; el arranque lento es un precio
    # ridículo al lado de esa confusión.
    #
    # sys.executable -m alembic: el binario del venv no está en PATH cuando
    # pytest corre desde un entorno distinto.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DSD_DATABASE_URL": URL_PRUEBAS},
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )


@pytest.fixture(scope="session", autouse=True)
def esquema() -> None:
    os.environ.setdefault(
        "DSD_JWT_SECRETO", "secreto-de-pruebas-de-32-bytes-o-mas-no-usar-en-produccion"
    )
    os.environ["DSD_DATABASE_URL"] = URL_PRUEBAS
    _preparar_esquema()


@pytest_asyncio.fixture
async def motor(esquema):  # noqa: ARG001
    m = create_async_engine(URL_PRUEBAS, poolclass=None)
    async with m.begin() as con:
        await con.execute(
            text(f"TRUNCATE {', '.join(TABLAS_VOLATILES)} RESTART IDENTITY CASCADE")
        )
    yield m
    await m.dispose()


@pytest_asyncio.fixture
async def sesion(motor) -> AsyncIterator[AsyncSession]:
    fabrica = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)
    async with fabrica() as s:
        yield s


@pytest_asyncio.fixture
async def cliente(motor) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app, con la sesión apuntando a la BD de pruebas."""
    from app.core.db import obtener_sesion
    from app.main import crear_app

    fabrica = async_sessionmaker(motor, expire_on_commit=False, class_=AsyncSession)

    async def sesion_de_pruebas() -> AsyncIterator[AsyncSession]:
        async with fabrica() as s:
            yield s

    app = crear_app()
    app.dependency_overrides[obtener_sesion] = sesion_de_pruebas
    # base_url con **https**, no http.
    #
    # La cookie de sesión del panel se marca `Secure` fuera de modo depuración, y
    # un cliente HTTP no manda una cookie Secure sobre http://. Con http, el
    # login "funcionaba" y todas las pantallas redirigían al login otra vez —un
    # síntoma que parece de sesión y es de esquema—. ASGITransport no abre un
    # socket, así que el esquema es solo una etiqueta: poner el correcto ejercita
    # el camino de producción.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://pruebas"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Semilla mínima
# ---------------------------------------------------------------------------

PASSWORD_VENDEDOR = "Vendedor2026"


@pytest_asyncio.fixture
async def semilla(sesion: AsyncSession) -> dict[str, uuid.UUID]:
    """Un vendedor con su camión y su ruta, y un admin."""
    from app.core.seguridad import hashear_password

    ahora = datetime.now(UTC)
    ids = {
        "sucursal": uuid.uuid4(),
        "vendedor": uuid.uuid4(),
        "admin": uuid.uuid4(),
        "camion": uuid.uuid4(),
        "bodega": uuid.uuid4(),
        "ruta": uuid.uuid4(),
    }
    hash_password = hashear_password(PASSWORD_VENDEDOR)

    # Roles y permisos ya vienen de la migración 0009.
    await sesion.execute(
        text("INSERT INTO sucursales(id, codigo, nombre) VALUES (:id,'MATRIZ','Matriz')"),
        {"id": ids["sucursal"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO usuarios(id, sucursal_id, codigo, nombre, password_hash, rol_codigo, "
            "creado_en, actualizado_en) VALUES "
            "(:v,:s,'VEND01','Juan Pérez',:h,'vendedor',:t,:t), "
            "(:a,:s,'ADMIN01','Bryan',:h,'admin',:t,:t)"
        ),
        {
            "v": ids["vendedor"],
            "a": ids["admin"],
            "s": ids["sucursal"],
            "h": hash_password,
            "t": ahora,
        },
    )
    await sesion.execute(
        text(
            "INSERT INTO almacenes(id, codigo, nombre, tipo, responsable_id) VALUES "
            "(:b,'BODEGA_PRINCIPAL','Bodega','bodega',NULL), "
            "(:c,'CAMION_01','Camión 01','camion',:v)"
        ),
        {"b": ids["bodega"], "c": ids["camion"], "v": ids["vendedor"]},
    )
    await sesion.execute(
        text("UPDATE usuarios SET almacen_id = :c WHERE id = :v"),
        {"c": ids["camion"], "v": ids["vendedor"]},
    )
    await sesion.execute(
        text(
            "INSERT INTO rutas(id, codigo, nombre, vendedor_id) VALUES (:r,'R04','Ruta 4',:v)"
        ),
        {"r": ids["ruta"], "v": ids["vendedor"]},
    )
    await sesion.execute(
        text("INSERT INTO usuarios_rutas(usuario_id, ruta_id) VALUES (:v,:r)"),
        {"v": ids["vendedor"], "r": ids["ruta"]},
    )
    await sesion.commit()
    ids["lista_precios"] = (
        await sesion.execute(text("SELECT id FROM listas_precios WHERE codigo = 'GENERAL'"))
    ).scalar_one()
    return ids
