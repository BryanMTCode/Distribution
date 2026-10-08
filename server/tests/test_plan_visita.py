"""El plan de visita (migración 0041): qué clientes tocan cada día.

Sin plan, «visita perdida» solo contaba las visitas que dejaron papel; el cliente
al que nadie fue no dejaba rastro. Lo que estas pruebas defienden:

1. El plan **llega al teléfono** dentro del delta del cliente, UNA vez por
   guardado aunque se escriban varios días.
2. Guardar el plan **no reescribe** lo que no cambió: el `desde` de un día ya
   planeado se conserva, o Efectividad perdería su historia.
3. La regla de «toca hoy» en SQL dice lo mismo que la de Dart (domingo, quinta
   semana).
4. Efectividad cuenta **lo que tocaba y nadie visitó**, solo días completos y solo
   desde que el plan existe.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR, solo_texto
from tests.conftest import csrf_del_panel as _csrf

pytestmark = pytest.mark.asyncio


async def _entrar(cliente, codigo: str = "ADMIN01") -> None:
    r = await cliente.post(
        "/panel/entrar",
        data={"codigo": codigo, "password": PASSWORD_VENDEDOR},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text


async def _cliente(sesion, semilla, nombre: str, secuencia: int | None = None) -> uuid.UUID:
    nuevo = uuid.uuid4()
    await sesion.execute(
        text(
            "INSERT INTO clientes (id, codigo, nombre_comercial, ruta_id, secuencia, "
            "                      creado_en, actualizado_en) "
            "VALUES (:id, :c, :n, :r, :s, now(), now())"
        ),
        {
            "id": nuevo,
            "c": f"C-{str(nuevo)[:8]}",
            "n": nombre,
            "r": semilla["ruta"],
            "s": secuencia,
        },
    )
    await sesion.commit()
    return nuevo


async def _plan(sesion, cliente_id) -> set:
    filas = (
        await sesion.execute(
            text(
                "SELECT dia_semana, semana_del_mes FROM clientes_frecuencia  WHERE cliente_id = :c"
            ),
            {"c": cliente_id},
        )
    ).all()
    return {(d, s) for d, s in filas}


async def _deltas_del_cliente(sesion, cliente_id) -> int:
    return (
        await sesion.execute(
            text("SELECT count(*) FROM change_log WHERE entidad = 'cliente' AND entidad_id = :c"),
            {"c": cliente_id},
        )
    ).scalar_one()


# ===========================================================================
# 1. Al teléfono, dentro del cliente
# ===========================================================================
async def test_el_plan_viaja_en_el_delta_del_cliente_una_sola_vez(sesion, semilla):
    cliente_id = await _cliente(sesion, semilla, "La Esquina")
    antes = await _deltas_del_cliente(sesion, cliente_id)

    # Tres días en UNA sentencia: el disparador es por sentencia.
    await sesion.execute(
        text(
            "INSERT INTO clientes_frecuencia (cliente_id, dia_semana) "
            "VALUES (:c, 1), (:c, 3), (:c, 5)"
        ),
        {"c": cliente_id},
    )
    await sesion.commit()

    assert await _deltas_del_cliente(sesion, cliente_id) == antes + 1
    payload = (
        await sesion.execute(
            text(
                "SELECT payload FROM change_log WHERE entidad = 'cliente' AND entidad_id = :c "
                " ORDER BY cursor DESC LIMIT 1"
            ),
            {"c": cliente_id},
        )
    ).scalar_one()
    assert payload["plan_visita"] == [
        {"dia": 1, "semana": None},
        {"dia": 3, "semana": None},
        {"dia": 5, "semana": None},
    ]


async def test_quitar_el_plan_tambien_viaja(sesion, semilla):
    cliente_id = await _cliente(sesion, semilla, "La Esquina")
    await sesion.execute(
        text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, 2)"),
        {"c": cliente_id},
    )
    await sesion.commit()
    await sesion.execute(
        text("DELETE FROM clientes_frecuencia WHERE cliente_id = :c"), {"c": cliente_id}
    )
    await sesion.commit()
    plan = (
        await sesion.execute(
            text("SELECT plan_visita FROM clientes WHERE id = :c"), {"c": cliente_id}
        )
    ).scalar_one()
    assert plan == []


# ===========================================================================
# 2. La regla, igual que en Dart
# ===========================================================================
@pytest.mark.parametrize(
    ("dia", "dow", "semana", "toca"),
    [
        ("2026-10-05", 1, None, True),  # lunes
        ("2026-10-04", 0, None, True),  # DOMINGO es 0
        ("2026-10-04", 6, None, False),
        ("2026-10-05", 1, 1, True),  # 1ª semana
        ("2026-10-12", 1, 1, False),  # 2ª semana
        ("2026-10-23", 5, 4, True),  # viernes de la 4ª
        ("2026-10-30", 5, 4, False),  # el 30 es la 5ª: «solo la 4ª» no toca
        ("2026-10-30", 5, None, True),  # «todas las semanas» sí
    ],
)
async def test_toca_visita_dice_lo_mismo_que_dart(sesion, dia, dow, semana, toca):
    resultado = (
        await sesion.execute(
            text(
                "SELECT toca_visita(CAST(:d AS date), CAST(:dow AS smallint), CAST(:s AS smallint))"
            ),
            {"d": dia, "dow": dow, "s": semana},
        )
    ).scalar_one()
    assert resultado is toca


# ===========================================================================
# 3. La pantalla de la ruta
# ===========================================================================
async def test_planear_la_ruta_de_una_vez(cliente, sesion, semilla):
    uno = await _cliente(sesion, semilla, "Abarrotes Uno", 1)
    dos = await _cliente(sesion, semilla, "Abarrotes Dos", 2)
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    assert pagina.status_code == 200
    assert "Abarrotes Uno" in solo_texto(pagina)

    r = await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={
            "csrf": _csrf(cliente, pagina),
            f"orden_{uno}": "2",
            f"orden_{dos}": "1",
            f"dia_{uno}_1": "1",
            f"dia_{uno}_4": "1",
            f"dia_{dos}_2": "1",
        },
        follow_redirects=True,
    )
    assert "Plan guardado: 2 cliente(s)" in solo_texto(r)
    assert await _plan(sesion, uno) == {(1, None), (4, None)}
    assert await _plan(sesion, dos) == {(2, None)}
    ordenes = dict(
        (
            await sesion.execute(
                text("SELECT id, secuencia FROM clientes WHERE id IN (:a, :b)"),
                {"a": uno, "b": dos},
            )
        ).all()
    )
    assert ordenes == {uno: 2, dos: 1}


async def test_un_dia_que_sigue_igual_conserva_su_desde(cliente, sesion, semilla):
    """Si cada guardado reescribiera el plan, la historia de cumplimiento se movería."""
    uno = await _cliente(sesion, semilla, "Abarrotes Uno", 1)
    hace_un_mes = date.today() - timedelta(days=30)
    await sesion.execute(
        text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana, desde) VALUES (:c, 1, :d)"),
        {"c": uno, "d": hace_un_mes},
    )
    await sesion.commit()
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={
            "csrf": _csrf(cliente, pagina),
            f"orden_{uno}": "1",
            f"dia_{uno}_1": "1",
            f"dia_{uno}_3": "1",
        },
    )
    desdes = dict(
        (
            await sesion.execute(
                text("SELECT dia_semana, desde FROM clientes_frecuencia WHERE cliente_id = :c"),
                {"c": uno},
            )
        ).all()
    )
    assert desdes[1] == hace_un_mes  # el lunes sigue contando desde hace un mes
    assert desdes[3] == date.today()  # el miércoles nuevo, desde hoy


async def test_un_plan_por_semanas_no_se_aplana_desde_la_ruta(cliente, sesion, semilla):
    quincenal = await _cliente(sesion, semilla, "Quincenal", 1)
    await sesion.execute(
        text(
            "INSERT INTO clientes_frecuencia (cliente_id, dia_semana, semana_del_mes) "
            "VALUES (:c, 1, 1), (:c, 1, 3)"
        ),
        {"c": quincenal},
    )
    await sesion.commit()
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    assert "se edita en su ficha" in solo_texto(pagina)
    await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={"csrf": _csrf(cliente, pagina), f"orden_{quincenal}": "1", f"dia_{quincenal}_1": "1"},
    )
    assert await _plan(sesion, quincenal) == {(1, 1), (1, 3)}


async def test_un_orden_con_letras_no_guarda_nada(cliente, sesion, semilla):
    uno = await _cliente(sesion, semilla, "Abarrotes Uno", 1)
    dos = await _cliente(sesion, semilla, "Abarrotes Dos", 2)
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    r = await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={
            "csrf": _csrf(cliente, pagina),
            f"orden_{uno}": "1",
            f"dia_{uno}_1": "1",
            f"orden_{dos}": "dos",
        },
        follow_redirects=True,
    )
    assert "no es un número" in solo_texto(r)
    assert await _plan(sesion, uno) == set()


# ===========================================================================
# 4. La ficha del cliente: semanas del mes
# ===========================================================================
async def test_la_ficha_guarda_un_plan_quincenal(cliente, sesion, semilla):
    cliente_id = await _cliente(sesion, semilla, "Quincenal")
    await _entrar(cliente)
    pagina = await cliente.get(f"/panel/clientes/{cliente_id}")
    assert 'id="forma_visita"' in pagina.text
    await cliente.post(
        f"/panel/clientes/{cliente_id}/visita",
        data={
            "csrf": _csrf(cliente, pagina),
            "dia_1": "1",
            "dia_4": "1",
            "semana_1": "1",
            "semana_3": "1",
        },
    )
    assert await _plan(sesion, cliente_id) == {(1, 1), (1, 3), (4, 1), (4, 3)}

    # Sin días, sale del plan.
    pagina = await cliente.get(f"/panel/clientes/{cliente_id}")
    r = await cliente.post(
        f"/panel/clientes/{cliente_id}/visita",
        data={"csrf": _csrf(cliente, pagina)},
        follow_redirects=True,
    )
    assert "Se quitó del plan" in solo_texto(r)
    assert await _plan(sesion, cliente_id) == set()


# ===========================================================================
# 5. Efectividad: lo que tocaba y nadie visitó
# ===========================================================================
async def _no_drop(sesion, semilla, cliente_id, dia) -> None:
    dispositivo = (
        await sesion.execute(
            text("SELECT id FROM dispositivos WHERE usuario_id = :u"), {"u": semilla["vendedor"]}
        )
    ).scalar_one_or_none()
    if dispositivo is None:
        dispositivo = uuid.uuid4()
        await sesion.execute(
            text(
                "INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
                "VALUES (:d, :u, 'Moto', 'activo', now())"
            ),
            {"d": dispositivo, "u": semilla["vendedor"]},
        )
    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, motivo_codigo, lat, lng,
                                  fecha_dispositivo, fecha_operativa)
            VALUES (:id, :d, :f, :c, :v, 'CERRADO', 21.5, -101.1, now(), :dia)
            """
        ),
        {
            "id": uuid.uuid4(),
            "d": dispositivo,
            "f": abs(hash((str(cliente_id), str(dia)))) % 100000 + 1,
            "c": cliente_id,
            "v": semilla["vendedor"],
            "dia": dia,
        },
    )
    await sesion.commit()


async def test_efectividad_cuenta_lo_que_tocaba_y_nadie_visito(cliente, sesion, semilla):
    """Un cliente planeado TODOS los días desde hace una semana; se le visitó uno.

    Hoy no cuenta (el día no ha terminado): son 7 días completos, 1 hecho, 6 no.
    """
    hoy = date.today()
    cliente_id = await _cliente(sesion, semilla, "Abarrotes Olvidado")
    await sesion.execute(
        text(
            "INSERT INTO clientes_frecuencia (cliente_id, dia_semana, desde) "
            "SELECT :c, d, :desde FROM generate_series(0, 6) AS d"
        ),
        {"c": cliente_id, "desde": hoy - timedelta(days=7)},
    )
    await sesion.commit()
    await _no_drop(sesion, semilla, cliente_id, hoy - timedelta(days=2))
    await _no_drop(sesion, semilla, cliente_id, hoy)  # hoy: no cuenta todavía

    await _entrar(cliente)
    r = await cliente.get(
        f"/panel/efectividad?desde={(hoy - timedelta(days=10)).isoformat()}&hasta={hoy.isoformat()}"
    )
    plano = solo_texto(r)
    assert "1 de 7 visitas del plan hechas" in plano
    assert "6 tocaban y nadie fue" in plano
    assert "Abarrotes Olvidado" in plano  # en «los que más se saltan»


async def test_el_plan_no_cuenta_antes_de_existir(cliente, sesion, semilla):
    """Capturar el plan hoy no convierte la semana pasada en visitas perdidas."""
    hoy = date.today()
    cliente_id = await _cliente(sesion, semilla, "Recién planeado")
    await sesion.execute(
        text(
            "INSERT INTO clientes_frecuencia (cliente_id, dia_semana) "
            "SELECT :c, d FROM generate_series(0, 6) AS d"
        ),
        {"c": cliente_id},
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await cliente.get(
        f"/panel/efectividad?desde={(hoy - timedelta(days=10)).isoformat()}&hasta={hoy.isoformat()}"
    )
    assert "Ningún cliente tenía visita planeada" in solo_texto(r)


# ===========================================================================
# 6. Quién planea
# ===========================================================================
async def test_gerencia_ve_el_plan_pero_no_lo_cambia(cliente, sesion, semilla):
    from app.core.seguridad import hashear_password

    uno = await _cliente(sesion, semilla, "Abarrotes Uno", 1)
    sucursal = (await sesion.execute(text("SELECT id FROM sucursales LIMIT 1"))).scalar_one()
    await sesion.execute(
        text(
            "INSERT INTO usuarios (id, sucursal_id, codigo, nombre, password_hash, rol_codigo, "
            "                      creado_en, actualizado_en) "
            "VALUES (:id, :s, 'GER01', 'Gerente', :h, 'gerente', now(), now())"
        ),
        {"id": uuid.uuid4(), "s": sucursal, "h": hashear_password(PASSWORD_VENDEDOR)},
    )
    await sesion.commit()
    await _entrar(cliente, "GER01")
    pagina = await cliente.get(f"/panel/plan-visita?ruta={semilla['ruta']}")
    assert pagina.status_code == 200
    assert "boton_guardar_plan" not in pagina.text
    r = await cliente.post(
        f"/panel/plan-visita/{semilla['ruta']}",
        data={"csrf": _csrf(cliente), f"orden_{uno}": "1", f"dia_{uno}_1": "1"},
    )
    assert r.status_code == 403
