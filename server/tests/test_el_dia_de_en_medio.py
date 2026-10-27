"""El día que ya contó un corte no lo vuelve a contar el siguiente (ADR 0002 §181).

«¿Por qué con un vendedor me aparece en su corte que se cargaron 2,389 a la
cuenta del vendedor?» La carga que se aceptó el sábado quedó «para el domingo» y
con ella vendió el lunes: su corte del lunes contó el lunes. La siguiente carga
fue del martes, y su corte arrancaba el día después de la FECHA de la anterior
—el lunes— otra vez: esperaba lo del lunes, ya entregado, más lo del martes, y
el lunes se le cargaba al vendedor como faltante de efectivo.

Ahora cada corte guarda hasta qué día contó, y el siguiente arranca el día
después.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infra.sync.manejadores import obtener_manejador
from tests.test_cierre_del_vendedor import MOMENTO, _aceptar, _ctx, _solicitud, dia  # noqa: F401
from tests.test_corte_sin_bajar_la_carga import _como_quedo, _vende_el_sabado
from tests.test_cortes_api import _cab

pytestmark = pytest.mark.asyncio


async def _corte(cliente, sesion, semilla, datos, *, el_dia, carga, entrega, para) -> dict:
    """El corte de ese día como lo manda el teléfono, y el gerente acepta la
    carga que pidió. Devuelve cómo quedó."""
    corte = uuid.uuid4()
    await obtener_manejador("corte.crear")(
        sesion, _ctx(datos, semilla), corte,
        {"fecha_operativa": el_dia.isoformat(), "carga_id": str(carga),
         "efectivo_declarado": entrega, "observaciones": None,
         "fecha_dispositivo": MOMENTO},
    )
    await sesion.commit()
    sid = await _solicitud(sesion, datos, semilla, corte_id=corte, para=para)
    r = await _aceptar(cliente, sid, await _cab(cliente, "GER01"))
    assert r.status_code == 200, r.text
    await sesion.rollback()
    return await _como_quedo(sesion, corte)


async def _carga_del(sesion, semilla, el_dia) -> uuid.UUID:
    return (
        await sesion.execute(
            text("SELECT id FROM cargas WHERE vendedor_id = :v AND fecha_operativa = :d"),
            {"v": semilla["vendedor"], "d": el_dia},
        )
    ).scalar_one()


async def _cargado_a_su_cuenta(sesion, semilla) -> Decimal:
    return (
        await sesion.execute(
            text("SELECT COALESCE(sum(importe), 0) FROM cuenta_vendedor "
                 " WHERE vendedor_id = :v AND tipo = 'cargo'"),
            {"v": semilla["vendedor"]},
        )
    ).scalar_one()


async def test_el_lunes_no_se_cuenta_otra_vez_en_el_corte_del_martes(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """El caso real: carga «para el domingo», venta y corte del lunes, carga y
    corte del martes. El martes espera solo lo del martes."""
    martes = dia["dia"]
    lunes, domingo = martes - timedelta(days=1), martes - timedelta(days=2)
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": domingo, "c": dia["carga"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET fecha_operativa = :d WHERE id = :v"),
        {"d": lunes, "v": dia["venta"]},
    )
    await sesion.commit()

    del_lunes = await _corte(cliente, sesion, semilla, dia, el_dia=lunes,
                             carga=dia["carga"], entrega="2250.00", para=martes)
    assert del_lunes["estado"] == "cerrado"
    assert del_lunes["efectivo_esperado"] == Decimal("2250.00")

    la_del_martes = await _carga_del(sesion, semilla, martes)
    await _vende_el_sabado(sesion, semilla, dia, carga=la_del_martes)
    del_martes = await _corte(cliente, sesion, semilla, dia, el_dia=martes,
                              carga=la_del_martes, entrega="1250.00",
                              para=martes + timedelta(days=1))

    assert del_martes["carga_id"] == la_del_martes
    assert (del_martes["efectivo_esperado"], del_martes["efectivo_entregado"]) == (
        Decimal("1250.00"), Decimal("1250.00")
    )
    assert del_martes["vendida"] == Decimal("100.000")
    assert await _cargado_a_su_cuenta(sesion, semilla) == Decimal("0")


async def test_lo_que_vendio_hoy_antes_de_que_aceptaran_el_corte_de_ayer(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """El gerente acepta el corte de ayer hoy en la mañana, y el vendedor ya
    salió a vender con lo que le sobró: lo de hoy no es del corte de ayer, es del
    de hoy. Antes, el de ayer lo esperaba y se le cargaba de faltante."""
    hoy = dia["dia"]
    ayer = hoy - timedelta(days=1)
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": ayer, "c": dia["carga"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET fecha_operativa = :d WHERE id = :v"),
        {"d": ayer, "v": dia["venta"]},
    )
    await sesion.commit()
    # Ya subió lo que vendió hoy, todavía con la carga de ayer.
    await _vende_el_sabado(sesion, semilla, dia, carga=dia["carga"])

    de_ayer = await _corte(cliente, sesion, semilla, dia, el_dia=ayer,
                           carga=dia["carga"], entrega="2250.00", para=hoy)
    assert (de_ayer["efectivo_esperado"], de_ayer["vendida"]) == (
        Decimal("2250.00"), Decimal("180.000")
    )
    assert await _cargado_a_su_cuenta(sesion, semilla) == Decimal("0")

    la_de_hoy = await _carga_del(sesion, semilla, hoy)
    de_hoy = await _corte(cliente, sesion, semilla, dia, el_dia=hoy, carga=la_de_hoy,
                          entrega="1250.00", para=hoy + timedelta(days=1))
    assert (de_hoy["efectivo_esperado"], de_hoy["vendida"]) == (
        Decimal("1250.00"), Decimal("100.000")
    )
    assert await _cargado_a_su_cuenta(sesion, semilla) == Decimal("0")


async def test_el_corte_guarda_hasta_que_dia_conto(cliente, sesion, semilla, dia):  # noqa: F811
    """El del vendedor, el día de su corte; el de la oficina, sin pasar de hoy."""
    from app.api.admin.liquidaciones import abrir_corte, cerrar_corte, guardar_arqueo

    lq = await abrir_corte(sesion, dia["carga"])
    await guardar_arqueo(sesion, lq, "2250", "")
    await cerrar_corte(sesion, lq, quien=semilla["admin"], confirmo_sincronizado=True,
                       conteo_automatico=True)
    hasta = (
        await sesion.execute(
            text("SELECT cuenta_hasta FROM liquidaciones WHERE id = :l"), {"l": lq}
        )
    ).scalar_one()
    assert hasta == dia["dia"]


async def _rellenar_como_la_migracion(sesion) -> None:
    """Los cortes cerrados antes de §181 no saben hasta qué día contaron: la
    migración lo llena."""
    import re

    from db.sql import leer_sql

    for actualizar in re.findall(
        r"UPDATE liquidaciones.*?;", leer_sql("0072_hasta_donde_conto.sql"), re.S
    ):
        await sesion.execute(text(actualizar))
    await sesion.commit()


async def test_el_corte_del_lunes_de_antes_tambien_sabe_hasta_que_dia_conto(
    cliente, sesion, semilla, dia  # noqa: F811
):
    """El del lunes se cerró antes del arreglo, sin `cuenta_hasta`: la migración
    le pone el día de su corte, y el del martes que se vuelve a cerrar ya no
    cuenta el lunes. Es LQ-000025: se elimina y se cierra otra vez."""
    martes = dia["dia"]
    lunes, domingo = martes - timedelta(days=1), martes - timedelta(days=2)
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": domingo, "c": dia["carga"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET fecha_operativa = :d WHERE id = :v"),
        {"d": lunes, "v": dia["venta"]},
    )
    await sesion.commit()
    del_lunes = await _corte(cliente, sesion, semilla, dia, el_dia=lunes,
                             carga=dia["carga"], entrega="2250.00", para=martes)
    await sesion.execute(text("UPDATE liquidaciones SET cuenta_hasta = NULL"))
    await sesion.commit()

    await _rellenar_como_la_migracion(sesion)

    hasta = (
        await sesion.execute(
            text("SELECT cuenta_hasta FROM liquidaciones WHERE id = :l"),
            {"l": del_lunes["liquidacion_id"]},
        )
    ).scalar_one()
    assert hasta == lunes

    la_del_martes = await _carga_del(sesion, semilla, martes)
    await _vende_el_sabado(sesion, semilla, dia, carga=la_del_martes)
    del_martes = await _corte(cliente, sesion, semilla, dia, el_dia=martes,
                              carga=la_del_martes, entrega="1250.00",
                              para=martes + timedelta(days=1))
    assert del_martes["efectivo_esperado"] == Decimal("1250.00")


async def test_el_de_la_oficina_de_antes_cuenta_hasta_antes_de_la_siguiente_carga(
    sesion, semilla, dia  # noqa: F811
):
    """Sin corte del vendedor: hasta antes de la siguiente carga, sin pasar del
    día en que se cerró."""
    from app.api.admin.liquidaciones import abrir_corte, cerrar_corte, guardar_arqueo

    hoy = dia["dia"]
    await sesion.execute(
        text("UPDATE cargas SET fecha_operativa = :d WHERE id = :c"),
        {"d": hoy - timedelta(days=3), "c": dia["carga"]},
    )
    await sesion.execute(
        text("UPDATE ventas SET fecha_operativa = :d WHERE id = :v"),
        {"d": hoy - timedelta(days=3), "v": dia["venta"]},
    )
    await sesion.commit()
    lq = await abrir_corte(sesion, dia["carga"])
    await guardar_arqueo(sesion, lq, "2250", "")
    await cerrar_corte(sesion, lq, quien=semilla["admin"], confirmo_sincronizado=True,
                       conteo_automatico=True)
    await sesion.execute(
        text(
            "INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id, "
            "  vendedor_id, ruta_id, fecha_operativa, estado, confirmada_en) "
            "VALUES (gen_random_uuid(), 'CG-SIG', :b, :c, :v, :r, :d, 'confirmada', now())"
        ),
        {"b": semilla["bodega"], "c": semilla["camion"], "v": semilla["vendedor"],
         "r": semilla["ruta"], "d": hoy - timedelta(days=1)},
    )
    await sesion.execute(text("UPDATE liquidaciones SET cuenta_hasta = NULL"))
    await sesion.commit()

    await _rellenar_como_la_migracion(sesion)

    hasta = (
        await sesion.execute(
            text("SELECT cuenta_hasta FROM liquidaciones WHERE id = :l"), {"l": lq}
        )
    ).scalar_one()
    assert hasta == hoy - timedelta(days=2)
