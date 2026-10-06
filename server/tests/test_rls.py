"""Seguridad por renglón: que la SEGUNDA cerradura de verdad cierre (Fase 9).

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTAS PRUEBAS NO SE PARECEN A LAS DEMÁS
────────────────────────────────────────────────────────────────────────────
Todo el resto de la suite corre como el rol **dueño** de las tablas, y en
PostgreSQL el dueño **salta sus propias políticas**. Eso significa que ni una
sola de las otras 681 pruebas ejerce RLS: podrían estar todas verdes con las
políticas escritas al revés.

Así que estas abren su propia conexión con `dsd_api` —el rol restringido, sin
BYPASSRLS— y aplican `db/ops/rol_api.sql` tal cual, sin copiarlo. Las dos cosas
a propósito:

· Si el archivo de operaciones se separa de lo que la API necesita —una tabla
  nueva sin GRANT, un DEFAULT PRIVILEGES olvidado— estas pruebas se ponen rojas
  antes de que el síntoma aparezca al desplegar.
· Y lo que se comprueba es el comportamiento de PostgreSQL, no el de una
  función de Python: `count(*)` sobre `clientes` con un alcance puesto.

────────────────────────────────────────────────────────────────────────────
LA PRUEBA QUE MÁS IMPORTA ES LA DEL ALCANCE VACÍO
────────────────────────────────────────────────────────────────────────────
Un sistema de autorización se rompe de dos formas, y solo una se nota: negar
de más molesta a alguien el mismo día, y **permitir de más no molesta a nadie
nunca**. El caso peligroso aquí es la conexión que viene del pool sin alcance
fijado: si eso devolviera la cartera completa, RLS estaría dando una falsa
sensación de seguridad justo en el camino que nadie mira.
"""

from __future__ import annotations

import os
import pathlib
import re
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import URL_PRUEBAS

CLAVE_API = "clave-de-pruebas-del-rol-api"

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def _url_del_rol_api() -> str:
    """La URL de pruebas, cambiando el usuario por `dsd_api`."""
    return re.sub(r"://[^@]+@", f"://dsd_api:{CLAVE_API}@", URL_PRUEBAS)


def _sql_del_rol(nombre_base: str) -> str:
    """`db/ops/rol_api.sql` sin las metaórdenes de psql.

    Se lee el archivo real en vez de repetir sus GRANT aquí: una copia se
    separa del original y entonces la prueba dejaría de probar lo que se
    despliega.
    """
    crudo = (RAIZ / "db" / "ops" / "rol_api.sql").read_text()
    sin_meta = "\n".join(
        linea for linea in crudo.splitlines() if not linea.startswith("\\")
    )
    return (
        sin_meta.replace(":'clave_api'", f"'{CLAVE_API}'")
        .replace(':"DBNAME"', f'"{nombre_base}"')
    )


@pytest.fixture(scope="session")
def rol_api(esquema) -> str:  # noqa: ARG001 — depende del esquema ya migrado
    """Crea el rol restringido en la base de pruebas, aplicando el archivo real."""
    import psycopg

    nombre_base = URL_PRUEBAS.rsplit("/", 1)[1]
    with psycopg.connect(URL_PRUEBAS.replace("+psycopg", ""), autocommit=True) as con:
        con.execute(_sql_del_rol(nombre_base))
    return _url_del_rol_api()


@pytest_asyncio.fixture
async def motor_restringido(rol_api):
    m = create_async_engine(rol_api, poolclass=None)
    yield m
    await m.dispose()


@pytest_asyncio.fixture
async def alcance(motor_restringido):
    """Devuelve una función que abre una conexión con el alcance pedido.

    Cada llamada abre su propia conexión: el alcance se fija a nivel de SESIÓN
    (ver `core/db.fijar_alcance`), así que reusar una conexión arrastraría el
    alcance de la prueba anterior — que es justo el fallo que la prueba del
    alcance vacío busca.
    """
    conexiones = []

    async def abrir(rol: str, usuario: uuid.UUID | None = None, rutas=()):
        conexion = await motor_restringido.connect()
        conexiones.append(conexion)
        await conexion.execute(
            text(
                "SELECT set_config('dsd.rol', :rol, false),"
                "       set_config('dsd.usuario_id', :usr, false),"
                "       set_config('dsd.rutas', :rutas, false)"
            ),
            {
                "rol": rol,
                "usr": str(usuario) if usuario else "",
                "rutas": ",".join(str(r) for r in rutas),
            },
        )
        return conexion

    yield abrir
    for conexion in conexiones:
        await conexion.close()


@pytest_asyncio.fixture
async def dos_rutas(sesion, semilla) -> dict:
    """Dos rutas con un cliente y una venta cada una, y dos vendedores.

    Las cifras no importan; lo que importa es que cada renglón tenga dueño
    distinto, para que «ver uno» y «ver los dos» sean resultados distinguibles.
    """
    from app.core.seguridad import hashear_password

    ids = {
        "ruta_a": semilla["ruta"],
        "ruta_b": uuid.uuid4(),
        "vendedor_a": semilla["vendedor"],
        "vendedor_b": uuid.uuid4(),
        "cliente_a": uuid.uuid4(),
        "cliente_b": uuid.uuid4(),
        "venta_a": uuid.uuid4(),
        "venta_b": uuid.uuid4(),
        "dispositivo_a": uuid.uuid4(),
        "dispositivo_b": uuid.uuid4(),
    }

    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, almacen_id, creado_en, actualizado_en) "
            "VALUES (:u, :s, 'VEND99', 'Otro vendedor', :h, 'vendedor', :a, now(), now())"
        ),
        {
            "u": ids["vendedor_b"],
            "s": semilla["sucursal"],
            "h": hashear_password("Vendedor2026"),
            "a": semilla["camion"],
        },
    )
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre, vendedor_id) VALUES (:r,'R99','Ruta 99',:v)"),
        {"r": ids["ruta_b"], "v": ids["vendedor_b"]},
    )
    for clave, ruta in (("cliente_a", "ruta_a"), ("cliente_b", "ruta_b")):
        await sesion.execute(
            text(
                "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, "
                "                      creado_en, actualizado_en) "
                "VALUES (:c, :cod, :nom, :r, now(), now())"
            ),
            {
                "c": ids[clave],
                "cod": clave[-1].upper() * 5,
                "nom": f"Cliente de la {ruta}",
                "r": ids[ruta],
            },
        )
    for sufijo in ("a", "b"):
        await sesion.execute(
            text(
                "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
                "VALUES (:d, :u, :e, 'activo', now())"
            ),
            {
                "d": ids[f"dispositivo_{sufijo}"],
                "u": ids[f"vendedor_{sufijo}"],
                "e": f"Equipo {sufijo}",
            },
        )
        await sesion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                    subtotal, total, fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, 1, :fl, :c, :u, :r, :a, 'contado',
                        100, 100, now(), CURRENT_DATE)
                """
            ),
            {
                "v": ids[f"venta_{sufijo}"],
                "d": ids[f"dispositivo_{sufijo}"],
                "fl": f"VEND-{sufijo}-000001",
                "c": ids[f"cliente_{sufijo}"],
                "u": ids[f"vendedor_{sufijo}"],
                "r": ids[f"ruta_{sufijo}"],
                "a": semilla["camion"],
            },
        )
    # Una cuenta por cobrar del cliente B, para probar el alcance heredado.
    await sesion.execute(
        text(
            """
            INSERT INTO cuentas_por_cobrar (venta_id, cliente_id, importe_original,
                                            fecha_emision, fecha_vencimiento, estado)
            VALUES (:v, :c, 100, CURRENT_DATE, CURRENT_DATE + 15, 'abierta')
            """
        ),
        {"v": ids["venta_b"], "c": ids["cliente_b"]},
    )
    # Y una partida de la venta A, para probar el alcance por EXISTS.
    producto = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base) "
            "VALUES (:p, 'RLS-1', 'Producto de prueba', 'PZA')"
        ),
        {"p": producto},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo,
                                        factor_unidad, cantidad, cantidad_base,
                                        precio_unitario, importe)
            VALUES (:id, :v, 1, :p, 'PZA', 1, 1, 1, 100, 100)
            """
        ),
        {"id": uuid.uuid4(), "v": ids["venta_a"], "p": producto},
    )
    await sesion.commit()
    return ids


@pytest_asyncio.fixture
async def cliente_rls(motor_restringido):
    """La API completa, hablando con PostgreSQL como `dsd_api`.

    ────────────────────────────────────────────────────────────────────────
    POR QUÉ ESTA FIXTURE ES LA MÁS IMPORTANTE DEL ARCHIVO
    ────────────────────────────────────────────────────────────────────────
    Las pruebas de arriba comprueban que RLS NIEGA lo que debe negar. Esta
    comprueba lo contrario, que es donde un despliegue se rompe de verdad: que
    la API sigue funcionando con el rol restringido.

    El modo de fallo que cubre es concreto y feo: se despliega con
    `DSD_DATABASE_URL_API`, nadie prueba el camino completo, y el lunes por la
    mañana el panel abre en blanco y el pull del teléfono devuelve cero
    registros. Las dos cosas sin ningún error en el log, porque «no ver nada» es
    una respuesta válida.

    Replica `obtener_sesion` tal cual, alcance anónimo incluido: si lo omitiera,
    la prueba pasaría por el camino que producción no usa.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.db import ROL_ANONIMO, fijar_alcance, obtener_sesion
    from app.main import crear_app

    fabrica = async_sessionmaker(motor_restringido, expire_on_commit=False)

    async def sesion_restringida():
        async with fabrica() as s:
            await fijar_alcance(s, rol=ROL_ANONIMO)
            yield s

    app = crear_app()
    app.dependency_overrides[obtener_sesion] = sesion_restringida
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://pruebas"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# El rol
# ---------------------------------------------------------------------------
async def test_el_rol_de_la_api_no_salta_las_politicas(motor_restringido):
    """Es la propiedad de la que depende todo lo demás.

    Con BYPASSRLS, `dsd_api` vería la cartera completa y la migración 0022
    sería decoración — y nada más en el sistema lo delataría.
    """
    async with motor_restringido.connect() as conexion:
        fila = (
            await conexion.execute(
                text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles "
                    " WHERE rolname = current_user"
                )
            )
        ).one()
    assert fila.rolbypassrls is False
    assert fila.rolsuper is False


async def test_el_rol_de_la_api_no_puede_crear_tablas(motor_restringido):
    """No tiene DDL: una inyección no puede dejar nada plantado en el esquema."""
    from sqlalchemy.exc import ProgrammingError

    with pytest.raises(ProgrammingError):
        async with motor_restringido.begin() as conexion:
            await conexion.execute(text("CREATE TABLE rls_intruso (x int)"))


# ---------------------------------------------------------------------------
# El alcance vacío: fallo cerrado
# ---------------------------------------------------------------------------
async def test_sin_alcance_no_se_ve_nada(motor_restringido, dos_rutas):
    """Una conexión del pool sin alcance fijado NO ve la cartera.

    Es el camino que nadie mira: un endpoint que se olvide de autenticar, o una
    conexión reciclada. Si esto devolviera filas, RLS estaría dando una falsa
    sensación de seguridad exactamente donde más duele.
    """
    async with motor_restringido.connect() as conexion:
        for tabla in ("clientes", "ventas", "cobros", "no_drops", "mermas", "change_log"):
            cuantos = (
                await conexion.execute(text(f"SELECT count(*) FROM {tabla}"))
            ).scalar_one()
            assert cuantos == 0, f"{tabla} se ve sin alcance"


async def test_el_rol_anonimo_tampoco_ve_nada(alcance, dos_rutas):
    """'anonimo' es la posición que fija `obtener_sesion` al abrir la sesión.

    No significa «sin restricción»: significa nada. Si significara lo primero,
    fijarlo sería peor que no fijarlo.
    """
    conexion = await alcance("anonimo")
    assert (
        await conexion.execute(text("SELECT count(*) FROM clientes"))
    ).scalar_one() == 0


async def test_un_vendedor_sin_rutas_no_ve_clientes(alcance, dos_rutas):
    conexion = await alcance("vendedor", dos_rutas["vendedor_a"], rutas=())
    assert (
        await conexion.execute(text("SELECT count(*) FROM clientes"))
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# El alcance por ruta
# ---------------------------------------------------------------------------
async def test_un_vendedor_ve_los_clientes_de_su_ruta_y_solo_esos(alcance, dos_rutas):
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    filas = (
        await conexion.execute(text("SELECT id FROM clientes"))
    ).scalars().all()
    assert filas == [dos_rutas["cliente_a"]]


async def test_pedir_por_id_un_cliente_de_otra_ruta_devuelve_vacio(alcance, dos_rutas):
    """El caso que una consulta sin filtro produciría: un `WHERE id = …` suelto.

    Es exactamente el bug que RLS existe para atrapar — la consulta se ve
    correcta, la pantalla funciona, y devuelve un cliente que no es de quien
    pregunta. Con la política, devuelve nada.
    """
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    fila = (
        await conexion.execute(
            text("SELECT nombre_comercial FROM clientes WHERE id = :c"),
            {"c": dos_rutas["cliente_b"]},
        )
    ).first()
    assert fila is None


async def test_un_vendedor_ve_sus_ventas_y_no_las_del_otro(alcance, dos_rutas):
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    filas = (await conexion.execute(text("SELECT id FROM ventas"))).scalars().all()
    assert filas == [dos_rutas["venta_a"]]


async def test_las_partidas_heredan_el_alcance_de_su_venta(alcance, dos_rutas):
    """El EXISTS de la política pasa por la política de `ventas`.

    Sin eso, `venta_partidas` sería la puerta de atrás: tiene producto, cantidad
    e importe de cada renglón de cada venta del negocio.
    """
    propio = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    assert (
        await propio.execute(text("SELECT count(*) FROM venta_partidas"))
    ).scalar_one() == 1

    ajeno = await alcance(
        "vendedor", dos_rutas["vendedor_b"], rutas=[dos_rutas["ruta_b"]]
    )
    assert (
        await ajeno.execute(text("SELECT count(*) FROM venta_partidas"))
    ).scalar_one() == 0


async def test_la_cartera_hereda_el_alcance_del_cliente(alcance, dos_rutas):
    de_la_b = await alcance(
        "vendedor", dos_rutas["vendedor_b"], rutas=[dos_rutas["ruta_b"]]
    )
    assert (
        await de_la_b.execute(text("SELECT count(*) FROM cuentas_por_cobrar"))
    ).scalar_one() == 1

    de_la_a = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    assert (
        await de_la_a.execute(text("SELECT count(*) FROM cuentas_por_cobrar"))
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# La oficina
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rol", ["admin", "gerente", "supervisor"])
async def test_los_roles_de_oficina_ven_la_operacion_completa(alcance, dos_rutas, rol):
    """Sin esto el panel saldría vacío, y el síntoma no apuntaría a RLS."""
    conexion = await alcance(rol, uuid.uuid4(), rutas=())
    assert (
        await conexion.execute(text("SELECT count(*) FROM clientes"))
    ).scalar_one() >= 2
    assert (
        await conexion.execute(text("SELECT count(*) FROM ventas"))
    ).scalar_one() >= 2


async def test_un_rol_inventado_no_ve_nada(alcance, dos_rutas):
    """La lista de roles de oficina es cerrada: lo que no está, no pasa."""
    conexion = await alcance("auditor_externo", uuid.uuid4(), rutas=())
    assert (
        await conexion.execute(text("SELECT count(*) FROM clientes"))
    ).scalar_one() == 0


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
async def test_un_vendedor_no_puede_escribir_una_venta_a_nombre_de_otro(
    alcance, dos_rutas
):
    """Es la mitad que la lectura no cubre.

    Sin el WITH CHECK, un equipo comprometido podría escribir ventas a nombre de
    otro vendedor — y la auditoría señalaría a la persona equivocada, que es
    peor que el robo que estaría ocultando.
    """
    from sqlalchemy.exc import ProgrammingError

    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    with pytest.raises(ProgrammingError, match="row-level security"):
        await conexion.execute(
            text(
                """
                INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                    cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                    subtotal, total, fecha_dispositivo, fecha_operativa)
                VALUES (:v, :d, 777, 'FALSA-000777', :c, :otro, :r, :a, 'contado',
                        1, 1, now(), CURRENT_DATE)
                """
            ),
            {
                "v": uuid.uuid4(),
                "d": dos_rutas["dispositivo_a"],
                "c": dos_rutas["cliente_a"],
                "otro": dos_rutas["vendedor_b"],
                "r": dos_rutas["ruta_a"],
                "a": (
                    await conexion.execute(
                        text("SELECT almacen_id FROM ventas WHERE id = :v"),
                        {"v": dos_rutas["venta_a"]},
                    )
                ).scalar_one(),
            },
        )


async def test_un_vendedor_si_puede_escribir_su_propia_venta(alcance, dos_rutas):
    """La otra mitad: la política no puede romper la operación normal."""
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    almacen = (
        await conexion.execute(
            text("SELECT almacen_id FROM ventas WHERE id = :v"),
            {"v": dos_rutas["venta_a"]},
        )
    ).scalar_one()
    await conexion.execute(
        text(
            """
            INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                                cliente_id, vendedor_id, ruta_id, almacen_id, tipo,
                                subtotal, total, fecha_dispositivo, fecha_operativa)
            VALUES (:v, :d, 778, 'PROPIA-000778', :c, :yo, :r, :a, 'contado',
                    1, 1, now(), CURRENT_DATE)
            """
        ),
        {
            "v": uuid.uuid4(),
            "d": dos_rutas["dispositivo_a"],
            "c": dos_rutas["cliente_a"],
            "yo": dos_rutas["vendedor_a"],
            "r": dos_rutas["ruta_a"],
            "a": almacen,
        },
    )
    await conexion.rollback()


async def test_nadie_borra_clientes(alcance, dos_rutas):
    """No hay política de DELETE, y la ausencia de política es una negación.

    Los clientes se marcan de baja; borrarlos dejaría ventas apuntando a un
    cliente que no existe, y el libro mayor sin a quién atribuirlas.
    """
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    resultado = await conexion.execute(
        text("DELETE FROM clientes WHERE id = :c"), {"c": dos_rutas["cliente_a"]}
    )
    # No lanza: la política simplemente no deja ver la fila para borrarla, así
    # que el DELETE afecta cero renglones. El efecto es el mismo y el síntoma es
    # mejor: nada se borró.
    assert resultado.rowcount == 0
    await conexion.rollback()


# ---------------------------------------------------------------------------
# change_log: el pull
# ---------------------------------------------------------------------------
async def test_el_catalogo_compartido_sigue_llegando_al_telefono(alcance, dos_rutas):
    """Las filas sin ruta son el catálogo: el camión las necesita completas.

    Si la política las bloqueara, el vendedor se quedaría sin precios — y el
    síntoma sería «la app no tiene productos», que no se parece a un problema
    de permisos.
    """
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    compartidas = (
        await conexion.execute(
            text("SELECT count(*) FROM change_log WHERE ruta_id IS NULL")
        )
    ).scalar_one()
    assert compartidas > 0


async def test_el_pull_no_entrega_los_deltas_de_otra_ruta(alcance, dos_rutas):
    conexion = await alcance(
        "vendedor", dos_rutas["vendedor_a"], rutas=[dos_rutas["ruta_a"]]
    )
    ajenas = (
        await conexion.execute(
            text("SELECT count(*) FROM change_log WHERE ruta_id = :r"),
            {"r": dos_rutas["ruta_b"]},
        )
    ).scalar_one()
    assert ajenas == 0


# ---------------------------------------------------------------------------
# Las dos listas de roles no se pueden separar
# ---------------------------------------------------------------------------
async def test_los_roles_de_oficina_coinciden_con_el_codigo(sesion):
    """`dsd_ve_todo()` y `Actor.alcanza_ruta()` tienen la misma lista.

    Están duplicadas por necesidad —una vive en SQL y la otra en Python— y si se
    separaran, la de PostgreSQL es la que manda: una consulta correcta en Python
    devolvería vacío, o peor, una que Python niega pasaría. Esta prueba no deja
    que la diferencia sea silenciosa.
    """
    from app.api.deps import ROLES_DE_OFICINA, Actor

    cuerpo = (
        await sesion.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'dsd_ve_todo'")
        )
    ).scalar_one()

    en_sql = set(re.findall(r"'(\w+)'", cuerpo))
    assert en_sql == set(ROLES_DE_OFICINA), (
        f"dsd_ve_todo() dice {sorted(en_sql)} y ROLES_DE_OFICINA "
        f"{sorted(ROLES_DE_OFICINA)}"
    )

    # Y la constante es la que `alcanza_ruta()` usa de verdad: comparar solo las
    # dos listas dejaría pasar que alguien reescribiera el método sin tocarla.
    for rol in ROLES_DE_OFICINA:
        actor = Actor(usuario_id=uuid.uuid4(), rol=rol)
        assert actor.alcanza_ruta(uuid.uuid4()), rol
    sin_rutas = Actor(usuario_id=uuid.uuid4(), rol="vendedor")
    assert not sin_rutas.alcanza_ruta(uuid.uuid4())


def test_la_configuracion_exige_el_rol_en_produccion():
    """Sin `DSD_DATABASE_URL_API`, la API de producción no arranca.

    Es el mismo criterio que el secreto JWT: lo que protege de verdad no puede
    quedar dependiendo de que alguien se acuerde de una variable de entorno.
    """
    from pydantic import ValidationError

    from app.core.config import Config

    secreto = os.environ.get(
        "DSD_JWT_SECRETO", "secreto-de-pruebas-de-32-bytes-o-mas-no-usar"
    )
    with pytest.raises(ValidationError, match="DSD_DATABASE_URL_API"):
        Config(entorno="produccion", jwt_secreto=secreto, database_url_api="")

    # Y con el rol puesto, sí arranca y lo reporta.
    cfg = Config(
        entorno="produccion",
        jwt_secreto=secreto,
        database_url_api="postgresql+psycopg://dsd_api:x@localhost/dsd",
    )
    assert cfg.rls_activa is True
    assert cfg.url_de_la_api != cfg.database_url


# ---------------------------------------------------------------------------
# La API completa bajo el rol restringido
# ---------------------------------------------------------------------------
async def test_el_panel_no_sale_en_blanco_con_rls(cliente_rls, sesion, dos_rutas):
    """El fallo que un despliegue con RLS produce si algo falta: todo vacío.

    Sin el `fijar_alcance` de `sesion_web`, o sin un GRANT, el panel abriría sin
    un error y sin datos — y el síntoma no apuntaría a la base.
    """
    from tests.conftest import PASSWORD_VENDEDOR, solo_texto

    entrada = await cliente_rls.post(
        "/panel/entrar",
        data={"codigo": "ADMIN01", "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert entrada.status_code == 303, entrada.text

    pantalla = await cliente_rls.get("/panel/clientes?filtro=todos")
    assert pantalla.status_code == 200
    texto = solo_texto(pantalla)
    # Los dos clientes, de las dos rutas: el admin ve la operación completa.
    assert "Cliente de la ruta_a" in texto, texto[-400:]
    assert "Cliente de la ruta_b" in texto


async def test_un_vendedor_lista_solo_su_ruta_a_traves_de_la_api(
    cliente_rls, sesion, dos_rutas
):
    """El camino real: token de vendedor, endpoint real, rol restringido.

    Aquí las dos cerraduras actúan juntas — el `WHERE` de la consulta y la
    política— y el resultado tiene que ser el mismo que con una sola.
    """
    from tests.conftest import PASSWORD_VENDEDOR

    await sesion.execute(
        text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r) "
             "ON CONFLICT DO NOTHING"),
        {"u": dos_rutas["vendedor_b"], "r": dos_rutas["ruta_b"]},
    )
    await sesion.commit()

    entrada = await cliente_rls.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND99",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(dos_rutas["dispositivo_b"]),
        },
    )
    assert entrada.status_code == 200, entrada.text
    cab = {"Authorization": f"Bearer {entrada.json()['access_token']}"}

    pagina = (await cliente_rls.get("/v1/clientes", headers=cab)).json()
    nombres = [c["nombre_comercial"] for c in pagina["clientes"]]
    assert nombres == ["Cliente de la ruta_b"]


async def test_el_pull_sigue_entregando_el_catalogo_bajo_rls(
    cliente_rls, sesion, dos_rutas
):
    """Si la política de `change_log` se pasara de estricta, el camión se
    quedaría sin precios — y el síntoma sería «la app no tiene productos»."""
    from tests.conftest import PASSWORD_VENDEDOR

    entrada = await cliente_rls.post(
        "/v1/auth/login",
        json={
            "codigo": "VEND99",
            "password": PASSWORD_VENDEDOR,
            "dispositivo_id": str(dos_rutas["dispositivo_b"]),
        },
    )
    cab = {"Authorization": f"Bearer {entrada.json()['access_token']}"}

    respuesta = await cliente_rls.get("/v1/sync/pull?cursor=0&limite=500", headers=cab)
    assert respuesta.status_code == 200, respuesta.text
    cambios = respuesta.json()["cambios"]
    assert cambios, "el pull no entregó nada: la política dejó al camión sin catálogo"

    # Y nada de la otra ruta.
    entidades_de_cliente = [
        c for c in cambios if c["entidad"] == "cliente"
    ]
    for cambio in entidades_de_cliente:
        assert cambio["entidad_id"] != str(dos_rutas["cliente_a"])


async def test_sin_token_la_api_no_filtra_nada_bajo_rls(cliente_rls, dos_rutas):
    """Un 401 sigue siendo un 401, no una respuesta vacía con 200.

    Importa porque bajo RLS hay un modo de fallo nuevo: que el endpoint
    responda 200 con cero resultados en vez de negar. Eso parecería «no hay
    datos» en vez de «no estás autenticado».
    """
    assert (await cliente_rls.get("/v1/clientes")).status_code == 401


# ---------------------------------------------------------------------------
# Los disparadores que alimentan `change_log`
# ---------------------------------------------------------------------------
# Estas pruebas no existían, y por eso un fallo que dejaba el panel en 500 llegó
# hasta un servidor de producción. Todas las de arriba prueban LECTURAS: que el
# rol restringido no vea lo que no le toca. Ninguna probaba una ESCRITURA, y las
# escrituras del catálogo disparan `fn_registrar_cambio`, que inserta en
# `change_log` — una tabla con RLS y sin política de INSERT.


@pytest.mark.asyncio
async def test_el_rol_restringido_puede_dar_de_alta_un_producto(alcance):
    """El síntoma era «Internal Server Error» al dar de alta un producto.

    Causa: `change_log` tiene RLS con política de SELECT y ninguna de INSERT, y
    la 0022 lo justificó diciendo que los disparadores «corren con los
    privilegios del dueño de la tabla». En PostgreSQL eso es falso: corren con
    los de QUIEN INVOCA salvo `SECURITY DEFINER`. Así que el disparador corría
    como `dsd_api` y PostgreSQL rechazaba su INSERT con «new row violates
    row-level security policy for table change_log».

    No se veía en desarrollo porque ahí la API usa el rol dueño, que salta las
    políticas de sus propias tablas: el fallo solo existe donde RLS está en uso.
    """
    con = await alcance("admin", usuario=uuid.uuid4())
    await con.execute(
        text(
            "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
            "VALUES (:id, 'RLS-ALTA-01', 'Producto de prueba', 'PZA', 0.16)"
        ),
        {"id": uuid.uuid4()},
    )
    cuantos = (
        await con.execute(
            text("SELECT count(*) FROM productos WHERE sku = 'RLS-ALTA-01'")
        )
    ).scalar_one()
    assert cuantos == 1, (
        "el rol restringido no pudo dar de alta un producto: revisa que los "
        "disparadores de change_log sigan siendo SECURITY DEFINER (migración 0029)"
    )


@pytest.mark.asyncio
async def test_el_rol_restringido_sigue_sin_poder_escribir_el_libro_de_cambios(alcance):
    """Y la intención de la 0022 se conserva: solo el disparador escribe ahí.

    El arreglo fue `SECURITY DEFINER` en las funciones, NO una política de
    INSERT en `change_log`. La diferencia importa: con una política, la API
    podría publicar al teléfono de un vendedor lo que quisiera. Con
    SECURITY DEFINER, solo puede hacerlo el disparador.
    """
    con = await alcance("admin", usuario=uuid.uuid4())
    with pytest.raises(Exception) as e:
        await con.execute(
            text(
                "INSERT INTO change_log (entidad, entidad_id, operacion, payload) "
                "VALUES ('producto', :id, 'insert', '{}'::jsonb)"
            ),
            {"id": uuid.uuid4()},
        )
    assert "row-level security" in str(e.value).lower(), (
        "la API puede escribir directamente en change_log: alguien le agregó una "
        f"política de INSERT. Error recibido: {e.value}"
    )


@pytest.mark.asyncio
async def test_el_rol_restringido_puede_cambiar_el_credito_de_un_cliente(alcance, sesion, semilla):
    """El mismo fallo de la 0029, en la función que esa migración no alcanzó.

    `fn_cartera_por_condiciones` (migración 0011) publica la cartera cuando cambia
    el límite, el permiso de crédito o el bloqueo de un cliente. No era
    `SECURITY DEFINER`, así que en producción —con RLS de verdad— subirle el
    límite a un cliente desde el panel terminaba en 500. La encontró la auditoría
    panel → teléfono de octubre de 2026; la corrige la 0042.
    """
    cliente_id = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, nombre_comercial, ruta_id, creado_en, actualizado_en) "
            "VALUES (:c, 'Abarrotes Lupita', :r, now(), now())"
        ),
        {"c": cliente_id, "r": semilla["ruta"]},
    )
    await sesion.commit()

    con = await alcance("admin", usuario=semilla["admin"])
    await con.execute(
        text(
            "UPDATE clientes SET permite_credito = true, limite_credito = 5000 "
            " WHERE id = :c"
        ),
        {"c": cliente_id},
    )
    await con.commit()

    cartera = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM change_log "
                " WHERE entidad = 'cartera' AND entidad_id = :c"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()
    assert cartera == 1


@pytest.mark.asyncio
async def test_toda_funcion_que_escribe_el_libro_de_cambios_corre_como_su_dueno(sesion, esquema):  # noqa: ARG001
    """La guardia general, para que el próximo disparador no repita la historia.

    Dos veces el mismo fallo —la 0029 y la 0042— en funciones que alguien escribió
    sin `SECURITY DEFINER`. Las pruebas de arriba lo detectan solo para las
    escrituras que alguien se acordó de probar con el rol restringido; esta lo
    detecta para TODAS, leyendo el catálogo de PostgreSQL.
    """
    sin_dueno = (
        await sesion.execute(
            text(
                """
                SELECT p.proname
                  FROM pg_proc p
                  JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'public'
                   AND p.prosrc ILIKE '%INSERT INTO change_log%'
                   AND NOT p.prosecdef
                 ORDER BY 1
                """
            )
        )
    ).scalars().all()
    assert sin_dueno == [], (
        f"publican al teléfono sin SECURITY DEFINER (en producción darían 500): {sin_dueno}"
    )
