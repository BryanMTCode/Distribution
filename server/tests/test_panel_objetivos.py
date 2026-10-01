"""La pantalla de objetivos: sin ella, la tarjeta de avance no tiene datos.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTAS PRUEBAS IMPORTAN MÁS DE LO QUE PARECE
────────────────────────────────────────────────────────────────────────────
La Fase 6 dejó una lección caraː se construyó la captura de motivos de merma y
no-drop, y **nada publicaba los catálogos al dispositivo**. La pantalla estaba
perfecta y no servía.

La tarjeta "avance vs objetivo" del tablero tiene el mismo riesgo. Estas pruebas
defienden las cuatro distinciones que lo hacen funcionar:

1. **Vacío borra, no guarda cero.** "Sin objetivo" y "objetivo $0" son dos
   cosas distintas, y la segunda da 100% de avance con la primera venta.
2. **Copiar no pisa.** Quien ajustó una ruta a mano este mes no quiere que un
   clic le devuelva la cifra del mes pasado.
3. **El permiso de ver y el de fijar son distintos.** Un supervisor puede ver
   cómo va el mes sin poder mover la meta.
4. **Un error de dedo se detiene.** Un objetivo con un cero de más deja la barra
   en 0.1% todo el mes y nadie sabe por qué.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.tablero import inicio_de_mes
from tests.conftest import PASSWORD_VENDEDOR, solo_texto

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


async def _crear_usuario(sesion, semilla, codigo: str, rol: str) -> uuid.UUID:
    from app.core.seguridad import hashear_password

    identificador = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, "
            "                      rol_codigo, creado_en, actualizado_en) "
            "VALUES (:u, :s, :cod, :nom, :h, :rol, now(), now())"
        ),
        {
            "u": identificador,
            "s": semilla["sucursal"],
            "cod": codigo,
            "nom": f"Usuario {codigo}",
            "h": hashear_password(PASSWORD_VENDEDOR),
            "rol": rol,
        },
    )
    await sesion.commit()
    return identificador


async def _csrf(cliente) -> str:
    r = await cliente.get("/panel/objetivos")
    import re

    coincidencia = re.search(r'name="csrf" value="([^"]+)"', r.text)
    assert coincidencia, "la pantalla no trajo token CSRF"
    return coincidencia.group(1)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
async def test_la_pantalla_lista_las_rutas_activas(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    assert r.status_code == 200
    texto = solo_texto(r)
    assert "R04" in texto
    assert "sin objetivo" in texto


async def test_las_rutas_sin_objetivo_se_cuentan(cliente, sesion, semilla):
    """Es la cifra que hace que alguien se acuerde de ponerles meta."""
    await _entrar(cliente)
    texto = solo_texto(await cliente.get("/panel/objetivos"))
    assert "rutas sin objetivo" in texto


async def test_un_mes_mal_escrito_no_tira_la_pantalla(cliente, sesion, semilla):
    """Lo que la persona quiere ver es un mes; el mes en curso es la respuesta útil."""
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos?mes=no-es-un-mes")
    assert r.status_code == 200
    assert inicio_de_mes(date.today()).strftime("%Y-%m") in r.text


async def test_con_objetivo_y_venta_se_dibuja_el_avance(cliente, sesion, semilla):
    """Ejercita la aritmética de la plantilla, que es donde han estado los bugs.

    Jinja mezcla `Decimal` (lo que viene de PostgreSQL) con `float` (lo que sale
    de un filtro) y con `int`, y un `Decimal / float` ahí revienta con un
    `TypeError` que solo se ve al abrir la pantalla con datos. Una prueba que
    solo renderiza la tabla vacía no toca ninguna de esas líneas.
    """
    periodo = inicio_de_mes(date.today())
    await sesion.execute(
        text(
            "INSERT INTO objetivos_ruta (ruta_id, periodo, objetivo_venta) "
            "VALUES (:r, :p, 100000)"
        ),
        {"r": semilla["ruta"], "p": periodo},
    )
    await sesion.execute(
        text(
            "INSERT INTO tablero_mes_ruta (periodo, ruta_id, venta_mes, visitas_mes) "
            "VALUES (:p, :r, 42500, 58)"
        ),
        {"r": semilla["ruta"], "p": periodo},
    )
    await sesion.commit()

    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    assert r.status_code == 200, r.text
    texto = solo_texto(r)
    assert "42.5%" in texto
    assert "$100,000.00" in texto
    assert "$42,500.00" in texto
    # Y el esperado al lado: sin él, 42.5% se lee igual el día 10 que el 28.
    assert "esperado" in texto


# ---------------------------------------------------------------------------
# Fijar
# ---------------------------------------------------------------------------
async def test_fijar_un_objetivo_lo_guarda_con_quien_lo_fijo(cliente, sesion, semilla):
    """Un objetivo sin autor es una decisión que nadie puede revisar después."""
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    periodo = inicio_de_mes(date.today())

    r = await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": csrf,
            "mes": periodo.strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "180,000.00",
            "objetivo_visitas": "520",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    fila = (
        await sesion.execute(
            text("SELECT * FROM objetivos_ruta WHERE ruta_id = :r AND periodo = :p"),
            {"r": semilla["ruta"], "p": periodo},
        )
    ).mappings().one()
    # La coma de "180,000.00" es lo que sale de copiar una celda de Excel.
    assert Decimal(fila["objetivo_venta"]) == Decimal("180000.00")
    assert fila["objetivo_visitas"] == 520
    assert fila["fijado_por"] == semilla["admin"]


async def test_un_objetivo_vacio_borra_el_renglon(cliente, sesion, semilla):
    """No guarda cero: un objetivo de $0 daría 100% con la primera venta."""
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    periodo = inicio_de_mes(date.today())
    datos = {
        "csrf": csrf,
        "mes": periodo.strftime("%Y-%m"),
        "ruta_id": str(semilla["ruta"]),
    }

    await cliente.post(
        "/panel/objetivos/fijar",
        data={**datos, "objetivo_venta": "90000"},
        follow_redirects=False,
    )
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_ruta"))
    ).scalar_one() == 1

    await cliente.post(
        "/panel/objetivos/fijar",
        data={**datos, "objetivo_venta": ""},
        follow_redirects=False,
    )
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_ruta"))
    ).scalar_one() == 0


async def test_cero_visitas_es_sin_meta_de_visitas_no_meta_de_cero(
    cliente, sesion, semilla
):
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    periodo = inicio_de_mes(date.today())
    await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": csrf,
            "mes": periodo.strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "90000",
            "objetivo_visitas": "",
        },
        follow_redirects=False,
    )
    visitas = (
        await sesion.execute(text("SELECT objetivo_visitas FROM objetivos_ruta"))
    ).scalar_one()
    assert visitas is None


async def test_un_objetivo_absurdo_se_detiene(cliente, sesion, semilla):
    """Un cero de más deja la barra en 0.1% todo el mes y nadie sabe por qué."""
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": csrf,
            "mes": inicio_de_mes(date.today()).strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "180000000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_ruta"))
    ).scalar_one() == 0


async def test_un_objetivo_negativo_se_detiene(cliente, sesion, semilla):
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": csrf,
            "mes": inicio_de_mes(date.today()).strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "-5000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


async def test_fijar_sin_csrf_no_pasa(cliente, sesion, semilla):
    await _entrar(cliente)
    r = await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": "token-inventado",
            "mes": inicio_de_mes(date.today()).strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "90000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Copiar
# ---------------------------------------------------------------------------
async def test_copiar_trae_los_del_mes_anterior(cliente, sesion, semilla):
    """Si fijar ocho rutas cuesta ocho capturas al mes, el mes con prisa no se fijan."""
    este_mes = inicio_de_mes(date.today())
    anterior = inicio_de_mes(este_mes.replace(day=1) - timedelta(days=1))
    await sesion.execute(
        text(
            "INSERT INTO objetivos_ruta (ruta_id, periodo, objetivo_venta, objetivo_visitas) "
            "VALUES (:r, :p, 150000, 480)"
        ),
        {"r": semilla["ruta"], "p": anterior},
    )
    await sesion.commit()

    await _entrar(cliente)
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/objetivos/copiar",
        data={"csrf": csrf, "mes": este_mes.strftime("%Y-%m")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    fila = (
        await sesion.execute(
            text("SELECT * FROM objetivos_ruta WHERE periodo = :p"), {"p": este_mes}
        )
    ).mappings().one()
    assert Decimal(fila["objetivo_venta"]) == Decimal("150000.00")
    assert fila["objetivo_visitas"] == 480


async def test_copiar_no_pisa_lo_que_ya_se_ajusto_a_mano(cliente, sesion, semilla):
    """El botón rellena huecos; no reinicia el mes."""
    este_mes = inicio_de_mes(date.today())
    anterior = inicio_de_mes(este_mes.replace(day=1) - timedelta(days=1))
    await sesion.execute(
        text(
            "INSERT INTO objetivos_ruta (ruta_id, periodo, objetivo_venta) VALUES "
            "(:r, :anterior, 150000), (:r, :este, 200000)"
        ),
        {"r": semilla["ruta"], "anterior": anterior, "este": este_mes},
    )
    await sesion.commit()

    await _entrar(cliente)
    csrf = await _csrf(cliente)
    await cliente.post(
        "/panel/objetivos/copiar",
        data={"csrf": csrf, "mes": este_mes.strftime("%Y-%m")},
        follow_redirects=False,
    )
    objetivo = (
        await sesion.execute(
            text("SELECT objetivo_venta FROM objetivos_ruta WHERE periodo = :p"),
            {"p": este_mes},
        )
    ).scalar_one()
    assert Decimal(objetivo) == Decimal("200000.00")


async def test_copiar_cuando_no_habia_nada_lo_dice(cliente, sesion, semilla):
    """Un aviso de "se copiaron 0" sin explicación parece una falla."""
    await _entrar(cliente)
    csrf = await _csrf(cliente)
    r = await cliente.post(
        "/panel/objetivos/copiar",
        data={"csrf": csrf, "mes": inicio_de_mes(date.today()).strftime("%Y-%m")},
        follow_redirects=False,
    )
    assert "No%20hab" in r.headers["location"] or "No había" in r.headers["location"]


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------
async def test_un_supervisor_ve_pero_no_fija(cliente, sesion, semilla):
    """Puede ver cómo va el mes sin poder mover la meta.

    `tablero.ver` y `objetivos.administrar` son permisos distintos a propósito:
    cambiar la meta cambia cómo se juzga a todo el equipo.
    """
    await _crear_usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")

    r = await cliente.get("/panel/objetivos")
    assert r.status_code == 200
    # Sin permiso de editar, la columna de captura no se dibuja.
    assert 'name="objetivo_venta"' not in r.text

    csrf_r = await cliente.get("/panel")
    import re

    csrf = re.search(r'name="csrf" value="([^"]+)"', csrf_r.text)
    prohibido = await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": csrf.group(1) if csrf else "",
            "mes": inicio_de_mes(date.today()).strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "90000",
        },
        follow_redirects=False,
    )
    assert prohibido.status_code == 403


async def test_un_gerente_si_fija_el_objetivo(cliente, sesion, semilla):
    """No contradice "gerencia es de solo lectura SOBRE LA OPERACIÓN".

    Un objetivo no mueve inventario ni dinero: es el plan contra el que se mide
    la operación. Si la única persona que mide no pudiera fijar contra qué, la
    tarjeta de avance quedaría vacía esperando que la oficina se acordara.
    """
    await _crear_usuario(sesion, semilla, "GER01", "gerente")
    await _entrar(cliente, "GER01")
    csrf = await _csrf(cliente)

    r = await cliente.post(
        "/panel/objetivos/fijar",
        data={
            "csrf": csrf,
            "mes": inicio_de_mes(date.today()).strftime("%Y-%m"),
            "ruta_id": str(semilla["ruta"]),
            "objetivo_venta": "120000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_ruta"))
    ).scalar_one() == 1
