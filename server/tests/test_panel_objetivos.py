"""Objetivos en el dashboard: solo el diario de cada vendedor, y todo se suma
(ADR 0002 §110, §115).

«Quiero que todo se sume.» Lo que defienden estas pruebas:

1. **Se captura una sola cosa**: el objetivo diario de cada vendedor. Ya no hay
   objetivo del mes ni de periodo que capturar aparte, ni uno de la empresa.
2. **La semana, el mes y cualquier periodo son la suma de sus días**, y lo
   esperado a la fecha es lo que suman los días ya transcurridos.
3. **Un día de descanso no suma**: el mes de quien no trabaja los domingos no
   cuenta los domingos.
4. **Toda la empresa es la suma de todos los vendedores**; el mayoreo va aparte.
5. **Ver y fijar son permisos distintos**: un supervisor ve, un gerente fija.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.domain.tablero import porcentaje
from tests.conftest import PASSWORD_VENDEDOR, csrf_del_panel, solo_texto

pytestmark = pytest.mark.asyncio

TODOS = (1, 2, 3, 4, 5, 6, 7)


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


async def _otra_ruta(sesion, semilla, codigo: str = "R05") -> uuid.UUID:
    ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas (id, codigo, nombre, vendedor_id) VALUES (:r, :c, :n, :v)"),
        {"r": ruta, "c": codigo, "n": f"Ruta {codigo[1:]}", "v": semilla["vendedor"]},
    )
    await sesion.commit()
    return ruta


async def _diario(sesion, ruta, monto: str, *, desde: date, dias=TODOS) -> None:
    """Un objetivo diario puesto directo: el panel no deja ponerlo más de 31
    días atrás, y aquí hace falta desde el día 1 del mes."""
    await sesion.execute(
        text("INSERT INTO objetivos_diarios (ruta_id, desde, objetivo_venta, dias_semana) "
             "VALUES (:r, :d, :m, CAST(:dias AS smallint[]))"),
        {"r": ruta, "d": desde, "m": monto, "dias": list(dias)},
    )
    await sesion.commit()


# ---------------------------------------------------------------------------
# Una sola captura
# ---------------------------------------------------------------------------
async def test_solo_se_captura_el_diario_de_cada_vendedor(cliente, sesion, semilla):
    await _otra_ruta(sesion, semilla)
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    assert r.status_code == 200
    # Cada vendedor activo tiene su renglón, aunque no tenga objetivo.
    assert f'id="diario_{semilla["ruta"]}"' in r.text
    assert 'id="sin_diario"' in r.text and 'id="diario_empresa"' in r.text
    # Ya no hay objetivo del mes ni de periodo que capturar, ni uno de la empresa.
    for ya_no in ('id="forma_por_periodo"', 'id="forma_mes_a_todas"', "/panel/objetivos/fijar",
                  "/panel/objetivos/copiar", "Toda la empresa (una meta"):
        assert ya_no not in r.text, ya_no
    assert (await cliente.post("/panel/objetivos/fijar", data={})).status_code in (404, 405)

    # A la empresa no se le pone: es la suma.
    r = await cliente.post(
        "/panel/objetivos/diario",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": "", "objetivo_venta": "3500",
              "dias": ["1", "2"]},
        follow_redirects=True,
    )
    assert "Escoge el vendedor, o «Todos los vendedores»." in solo_texto(r)


# ---------------------------------------------------------------------------
# Lo que suma
# ---------------------------------------------------------------------------
async def test_la_semana_y_el_mes_son_la_suma_de_sus_dias(cliente, sesion, semilla):
    hoy = date.today()
    otra = await _otra_ruta(sesion, semilla)
    await _diario(sesion, semilla["ruta"], "1000", desde=hoy.replace(day=1))
    await _diario(sesion, otra, "500", desde=hoy.replace(day=1))
    await _entrar(cliente)

    r = await cliente.get("/panel/objetivos?periodo=semana")
    plano = solo_texto(r)
    lunes = hoy - timedelta(days=hoy.weekday())
    # La semana: siete días de cada uno, salvo los de antes del día 1, cuando
    # todavía no había objetivo.
    dias = sum(1 for i in range(7) if lunes + timedelta(days=i) >= hoy.replace(day=1))
    assert f"R04 · Ruta 4 {dias} día(s) con objetivo ${1000 * dias:,}.00" in plano
    assert f"Toda la empresa la suma de todos ${1500 * dias:,}.00" in plano

    r = await cliente.get("/panel/objetivos")  # el mes, por omisión
    dias_del_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    plano = solo_texto(r)
    assert f"R04 · Ruta 4 {dias_del_mes} día(s) con objetivo ${1000 * dias_del_mes:,}.00" in plano
    assert f"Toda la empresa la suma de todos ${1500 * dias_del_mes:,}.00" in plano
    # Lo esperado es lo que suman los días transcurridos, hoy incluido.
    assert f"esperado {porcentaje(hoy.day, dias_del_mes)}%" in plano


async def test_un_dia_de_descanso_no_suma(cliente, sesion, semilla):
    """Quien no trabaja los domingos no carga los domingos en su mes."""
    hoy = date.today()
    await _diario(sesion, semilla["ruta"], "1000", desde=hoy.replace(day=1),
                  dias=(1, 2, 3, 4, 5, 6))
    await _entrar(cliente)
    r = await cliente.get("/panel/objetivos")
    dias = sum(
        1 for d in range(1, calendar.monthrange(hoy.year, hoy.month)[1] + 1)
        if date(hoy.year, hoy.month, d).isoweekday() != 7
    )
    assert f"R04 · Ruta 4 {dias} día(s) con objetivo ${1000 * dias:,}.00" in solo_texto(r)


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------
async def test_un_supervisor_ve_pero_no_fija(cliente, sesion, semilla):
    """`tablero.ver` y `objetivos.administrar` son permisos distintos a propósito:
    cambiar la meta cambia cómo se juzga a todo el equipo."""
    await _crear_usuario(sesion, semilla, "SUP01", "supervisor")
    await _entrar(cliente, "SUP01")
    r = await cliente.get("/panel/objetivos")
    assert r.status_code == 200
    assert 'id="forma_diario"' not in r.text and 'id="tabla_suma"' in r.text
    prohibido = await cliente.post(
        "/panel/objetivos/diario",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": str(semilla["ruta"]),
              "objetivo_venta": "3500", "dias": ["1"]},
        follow_redirects=False,
    )
    assert prohibido.status_code == 403


async def test_un_gerente_si_fija_el_objetivo(cliente, sesion, semilla):
    """Un objetivo no mueve inventario ni dinero: es el plan contra el que se mide
    la operación, y quien mide tiene que poder fijarlo."""
    await _crear_usuario(sesion, semilla, "GER01", "gerente")
    await _entrar(cliente, "GER01")
    r = await cliente.get("/panel/objetivos")
    r = await cliente.post(
        "/panel/objetivos/diario",
        data={"csrf": csrf_del_panel(cliente, r), "ruta_id": str(semilla["ruta"]),
              "objetivo_venta": "3500", "dias": ["1", "2", "3", "4", "5", "6"]},
        follow_redirects=True,
    )
    assert "Objetivo diario guardado: $3,500.00 cada día, de lunes a sábado" in solo_texto(r)
    assert (
        await sesion.execute(text("SELECT count(*) FROM objetivos_diarios"))
    ).scalar_one() == 1
