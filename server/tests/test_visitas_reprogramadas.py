"""Los días de visita desde el alta, y la visita reprogramada (ADR 0002 §103).

«Cuando los vendedores dan de alta una tienda, ellos pueden elegir qué días la
visitarán, y cuando los visiten esos días y el cliente no les va a comprar y les
dice "ven mañana" o tal día, les aparezca la opción de reprogramar esa visita
para el día que el cliente les solicita, pero la configuración inicial de días
sigue fija.»

1. **El alta trae sus días**: quedan en el plan del cliente y viajan al teléfono.
2. **«Ven mañana»** queda en el no-drop y en el cliente (`visita_reprogramada`),
   que viaja en su delta; los días fijos no se tocan.
3. **Una fecha imposible** no se guarda y la visita se marca para revisión.
4. **Efectividad** cuenta la visita reprogramada como una que tocaba.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import MOTIVO_REPROGRAMACION_INVALIDA
from tests.conftest import solo_texto
from tests.test_merma_ingesta import DIA, _aplicar, _no_drop, escenario  # noqa: F401
from tests.test_plan_visita import _cliente, _entrar

pytestmark = pytest.mark.asyncio


async def _plan(sesion, cliente_id) -> list[int]:
    return [
        d for (d,) in (
            await sesion.execute(
                text("SELECT dia_semana FROM clientes_frecuencia WHERE cliente_id = :c "
                     " ORDER BY dia_semana"),
                {"c": cliente_id},
            )
        ).all()
    ]


async def test_el_alta_trae_sus_dias_de_visita(sesion, semilla, escenario):  # noqa: F811
    cliente_id = await _aplicar(
        sesion, escenario, semilla, "cliente.crear",
        # Repetidos, fuera de rango y de otro tipo no pasan.
        {"nombre_comercial": "Tiendita Lupita", "dias_visita": [4, 1, 4, 9, "2", True]},
    )
    await sesion.commit()
    assert await _plan(sesion, cliente_id) == [1, 4]
    plan = (
        await sesion.execute(
            text("SELECT plan_visita FROM clientes WHERE id = :c"), {"c": cliente_id}
        )
    ).scalar_one()
    assert plan == [{"dia": 1, "semana": None}, {"dia": 4, "semana": None}]
    # Y viaja al teléfono en el delta del cliente.
    ultimo = (
        await sesion.execute(
            text("SELECT payload FROM change_log WHERE entidad = 'cliente' "
                 "   AND entidad_id = :c ORDER BY cursor DESC LIMIT 1"),
            {"c": cliente_id},
        )
    ).scalar_one()
    assert ultimo["plan_visita"] == plan


async def test_ven_manana_se_guarda_y_viaja_con_el_cliente(sesion, semilla, escenario):  # noqa: F811
    manana = (date.fromisoformat(DIA) + timedelta(days=1)).isoformat()
    await sesion.execute(
        text("INSERT INTO clientes_frecuencia (cliente_id, dia_semana) VALUES (:c, 1)"),
        {"c": escenario["cliente"]},
    )
    await sesion.commit()

    no_drop = await _aplicar(
        sesion, escenario, semilla, "no_drop.crear",
        _no_drop(escenario, reprogramada_para=manana),
    )
    await sesion.commit()

    fila = (
        await sesion.execute(
            text("SELECT reprogramada_para, requiere_revision FROM no_drops WHERE id = :n"),
            {"n": no_drop},
        )
    ).one()
    assert (fila.reprogramada_para.isoformat(), fila.requiere_revision) == (manana, False)
    reprogramada = (
        await sesion.execute(
            text("SELECT visita_reprogramada FROM clientes WHERE id = :c"),
            {"c": escenario["cliente"]},
        )
    ).scalar_one()
    assert reprogramada.isoformat() == manana
    # Los días fijos no cambian.
    assert await _plan(sesion, escenario["cliente"]) == [1]
    # El teléfono la recibe dentro del delta del cliente.
    ultimo = (
        await sesion.execute(
            text("SELECT payload FROM change_log WHERE entidad = 'cliente' "
                 "   AND entidad_id = :c ORDER BY cursor DESC LIMIT 1"),
            {"c": escenario["cliente"]},
        )
    ).scalar_one()
    assert ultimo["visita_reprogramada"] == manana


async def test_la_ficha_del_panel_dice_la_visita_reprogramada(cliente, sesion, semilla):
    cliente_id = await _cliente(sesion, semilla, "Abarrotes Jueves")
    await sesion.execute(
        text("UPDATE clientes SET visita_reprogramada = '2026-10-15' WHERE id = :c"),
        {"c": cliente_id},
    )
    await sesion.commit()
    await _entrar(cliente)
    r = await cliente.get(f"/panel/clientes/{cliente_id}")
    assert 'id="visita_reprogramada"' in r.text
    assert "jueves 15 de octubre de 2026" in solo_texto(r)


@pytest.mark.parametrize("dia", [DIA, "2026-09-01", "2026-12-25", "jueves"])
async def test_una_fecha_imposible_no_se_guarda_y_se_marca(sesion, semilla, escenario, dia):  # noqa: F811
    no_drop = await _aplicar(
        sesion, escenario, semilla, "no_drop.crear",
        _no_drop(escenario, reprogramada_para=dia),
    )
    await sesion.commit()
    fila = (
        await sesion.execute(
            text("SELECT reprogramada_para, requiere_revision, revision_motivos "
                 "  FROM no_drops WHERE id = :n"),
            {"n": no_drop},
        )
    ).one()
    # La visita se registra igual: el cliente no compró, eso pasó.
    assert fila.reprogramada_para is None
    assert fila.requiere_revision is True
    assert fila.revision_motivos == [MOTIVO_REPROGRAMACION_INVALIDA]


async def test_efectividad_cuenta_la_visita_reprogramada(cliente, sesion, semilla):
    """Sin plan fijo; el lunes pidió que volvieran el miércoles y nadie fue."""
    hoy = date.today()
    lunes, miercoles = hoy - timedelta(days=5), hoy - timedelta(days=3)
    cliente_id = await _cliente(sesion, semilla, "Abarrotes Ven Mañana")
    dispositivo = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d, :u, 'Moto', 'activo', now())"),
        {"d": dispositivo, "u": semilla["vendedor"]},
    )
    await sesion.execute(
        text(
            """
            INSERT INTO no_drops (id, dispositivo_id, folio_consecutivo, cliente_id,
                                  vendedor_id, motivo_codigo, lat, lng,
                                  fecha_dispositivo, fecha_operativa, reprogramada_para)
            VALUES (:id, :d, 1, :c, :v, 'CERRADO', 21.5, -101.1, now(), :lunes, :miercoles)
            """
        ),
        {"id": uuid.uuid4(), "d": dispositivo, "c": cliente_id, "v": semilla["vendedor"],
         "lunes": lunes, "miercoles": miercoles},
    )
    await sesion.commit()

    await _entrar(cliente)
    plano = solo_texto(
        await cliente.get(
            f"/panel/efectividad?desde={(hoy - timedelta(days=10)).isoformat()}"
            f"&hasta={hoy.isoformat()}"
        )
    )
    assert "0 de 1 visitas del plan hechas" in plano
    assert "Abarrotes Ven Mañana" in plano
