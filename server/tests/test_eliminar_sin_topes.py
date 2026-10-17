"""Eliminar sin topes desde el dashboard (ADR 0002 §126).

«Desde el dashboard quiero tener poder absoluto de eliminar lo que yo quiera y
que se anulen los efectos… principalmente cargas, ventas, movimientos del
almacén, cortes incluso.» Para probar con el almacén y luego borrar lo de prueba.

1. **El corte se deshace**: lo que le cargó al vendedor se condona, la carga
   vuelve a «por cortar» y el corte que mandó el vendedor a «por cerrar»; se
   puede cerrar otra vez.
2. **El corte contado regresa sus ajustes** al camión y al teléfono, y al
   volver a cortarlo el teléfono recibe solo los del corte nuevo.
3. **La carga se lleva su corte**, en cascada, y la solicitud que la pidió queda
   rechazada para que el vendedor pida otra.
4. **Lo que quedaría en negativo pide la casilla.**
5. **Varias a la vez, en orden**: primero la venta y después la carga, así nada
   queda en negativo a la mitad; la que no se puede dice por qué y las demás
   siguen. Lo eliminado sale de la lista de siempre.
6. **La compra pagada** se elimina con sus pagos.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import csrf_del_panel, solo_texto
from tests.test_cierre_del_vendedor import (  # noqa: F401
    _aceptar,
    _cerrar,
    _corte,
    _solicitud,
    dia,
)
from tests.test_cortes_api import _cab
from tests.test_operaciones import MOTIVO, _anular
from tests.test_panel_liquidacion import _entrar

pytestmark = pytest.mark.asyncio


async def _existencia(sesion, almacen, producto) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text("SELECT COALESCE((SELECT cantidad FROM existencias "
                     " WHERE almacen_id = :a AND producto_id = :p), 0)"),
                {"a": almacen, "p": producto},
            )
        ).scalar_one()
    )


async def _saldo(sesion, vendedor) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text("SELECT COALESCE((SELECT saldo FROM v_cuenta_vendedor "
                     " WHERE vendedor_id = :v), 0)"),
                {"v": vendedor},
            )
        ).scalar_one()
    )


async def _deltas_de_la_carga(sesion, carga) -> list[dict]:
    filas = (
        await sesion.execute(
            text("SELECT payload FROM change_log WHERE entidad = 'carga' "
                 "   AND entidad_id = :c ORDER BY cursor"),
            {"c": carga},
        )
    ).scalars().all()
    return [f if isinstance(f, dict) else json.loads(f) for f in filas]


async def _uno(sesion, sql, **params):
    return (await sesion.execute(text(sql), params)).mappings().one()


# ---------------------------------------------------------------------------
# El corte
# ---------------------------------------------------------------------------


async def test_el_corte_del_vendedor_se_deshace_y_vuelve_a_por_cerrar(
    cliente, sesion, semilla, dia  # noqa: F811
):
    corte_id = await _corte(sesion, dia, semilla, efectivo="2200.00")
    cab = await _cab(cliente, "GER01")
    await _cerrar(cliente, corte_id, cab)
    liquidacion = (
        await _uno(sesion, "SELECT liquidacion_id FROM cortes_vendedor WHERE id = :c",
                   c=corte_id)
    )["liquidacion_id"]
    # Le faltaron $50: se le cargaron a su cuenta.
    assert await _saldo(sesion, semilla["vendedor"]) == Decimal("50.00")

    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/corte/{liquidacion}"))
    assert "Si la eliminas" in pagina
    assert "Se le condonan a Juan Pérez los $50.00 que este corte le cargó" in pagina
    assert "La carga CG-TEST vuelve a «por cortar»." in pagina
    assert "vuelve a «Cortes por cerrar»" in pagina

    r = await _anular(cliente, "corte", liquidacion)
    assert "anulada" in solo_texto(r)

    corte = await _uno(sesion, "SELECT estado, folio FROM liquidaciones WHERE id = :l",
                       l=liquidacion)
    assert corte["estado"] == "anulada"
    # La cuenta queda como antes, con la condonación que apunta a ese corte.
    assert await _saldo(sesion, semilla["vendedor"]) == Decimal("0.00")
    condonacion = await _uno(
        sesion, "SELECT importe, concepto FROM cuenta_vendedor "
        " WHERE liquidacion_id = :l AND tipo = 'condonacion'", l=liquidacion,
    )
    assert condonacion["importe"] == Decimal("50.00")
    assert condonacion["concepto"].startswith(f"Corte {corte['folio']} anulado: ")
    # El del vendedor, por cerrar otra vez; la carga, por cortar.
    del_vendedor = await _uno(
        sesion, "SELECT estado, liquidacion_id, efectivo_recibido FROM cortes_vendedor "
        " WHERE id = :c", c=corte_id,
    )
    assert (del_vendedor["estado"], del_vendedor["liquidacion_id"]) == ("pendiente", None)
    assert (await _uno(sesion, "SELECT estado FROM cargas WHERE id = :c", c=dia["carga"]))[
        "estado"] == "confirmada"
    # El teléfono: la carga pasa por cancelada y vuelve a confirmada, así olvida
    # el cierre y la suma igual que antes.
    estados = [d["estado"] for d in await _deltas_de_la_carga(sesion, dia["carga"])]
    assert estados[-3:] == ["liquidada", "cancelada", "confirmada"]

    # En el panel: la carga está por cortar y el corte ya no sale en la lista.
    texto = solo_texto(await cliente.get("/panel/liquidaciones"))
    assert corte["folio"] not in texto and "CG-TEST" in texto
    # Y la página del corte dice que se eliminó.
    r = await cliente.get(f"/panel/liquidaciones/{liquidacion}")
    assert 'id="corte_eliminado"' in r.text

    # Se cierra otra vez, con otro corte y su cargo.
    await _cerrar(cliente, corte_id, cab)
    nuevo = (
        await _uno(sesion, "SELECT liquidacion_id FROM cortes_vendedor WHERE id = :c",
                   c=corte_id)
    )["liquidacion_id"]
    assert nuevo is not None and nuevo != liquidacion
    assert await _saldo(sesion, semilla["vendedor"]) == Decimal("50.00")


async def test_el_corte_contado_regresa_sus_ajustes_al_camion_y_al_telefono(
    cliente, sesion, semilla, dia  # noqa: F811
):
    from app.api.admin.liquidaciones import (
        abrir_corte,
        cerrar_corte,
        guardar_arqueo,
        guardar_conteo,
    )

    async def cortar(contadas: str) -> uuid.UUID:
        liquidacion = await abrir_corte(sesion, dia["carga"])
        renglon = (
            await sesion.execute(
                text("SELECT id FROM liquidacion_detalle WHERE liquidacion_id = :l"),
                {"l": liquidacion},
            )
        ).scalar_one()
        await guardar_conteo(sesion, liquidacion, {str(renglon): contadas})
        await guardar_arqueo(sesion, liquidacion, "2250.00", "")
        await cerrar_corte(sesion, liquidacion, quien=semilla["admin"],
                           confirmo_sincronizado=True)
        return liquidacion

    # Se contaron 55 de 60: el corte deja el camión en 55.
    primero = await cortar("55")
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 55

    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/corte/{primero}"))
    assert "Los ajustes que dejó el corte en" in pagina
    await _anular(cliente, "corte", primero)
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 60
    # El teléfono recibe el contrario como ajuste del camión.
    ajuste = await _uno(
        sesion, "SELECT tipo, delta FROM ajustes_camion WHERE almacen_id = :a",
        a=semilla["camion"],
    )
    assert (ajuste["tipo"], ajuste["delta"]) == ("anulacion", Decimal("5.000"))

    # Otra vez, contando 58: el teléfono recibe solo el ajuste del corte nuevo.
    await cortar("58")
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 58
    ultimo = (await _deltas_de_la_carga(sesion, dia["carga"]))[-1]
    assert ultimo["estado"] == "liquidada"
    assert [a["cantidad"] for a in ultimo["ajustes"]] == ["-2.000"]


# ---------------------------------------------------------------------------
# La carga, con su corte y su solicitud
# ---------------------------------------------------------------------------


async def test_eliminar_la_carga_se_lleva_su_corte(cliente, sesion, semilla, dia):  # noqa: F811
    corte_id = await _corte(sesion, dia, semilla, efectivo="2200.00")
    await _cerrar(cliente, corte_id, await _cab(cliente, "GER01"))
    liquidacion = (
        await _uno(sesion, "SELECT liquidacion_id FROM cortes_vendedor WHERE id = :c",
                   c=corte_id)
    )["liquidacion_id"]

    await _entrar(cliente)
    # Primero la venta (su corte ya se cerró: hay que confirmarlo).
    await _anular(cliente, "venta", dia["venta"], entiendo=True)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/carga/{dia['carga']}"))
    assert "Se elimina también su corte" in pagina
    r = await _anular(cliente, "carga", dia["carga"])
    assert "Carga CG-TEST anulada." in solo_texto(r)

    assert (await _uno(sesion, "SELECT estado FROM cargas WHERE id = :c", c=dia["carga"]))[
        "estado"] == "cancelada"
    assert (await _uno(sesion, "SELECT estado FROM liquidaciones WHERE id = :l",
                       l=liquidacion))["estado"] == "anulada"
    assert (await _uno(sesion, "SELECT estado FROM cortes_vendedor WHERE id = :c",
                       c=corte_id))["estado"] == "anulado"
    # El corte tiene su propia anulación, que dice que se fue con la carga.
    anulacion = await _uno(
        sesion, "SELECT motivo, datos FROM anulaciones WHERE tipo = 'corte' "
        "   AND documento_id = :l", l=liquidacion,
    )
    assert anulacion["motivo"] == f"Se eliminó con la carga CG-TEST: {MOTIVO}"
    assert anulacion["datos"]["con_la_carga"] == "CG-TEST"
    # Todo como antes del día: la bodega con sus 1,000, el camión vacío y la
    # cuenta del vendedor en cero.
    assert await _existencia(sesion, semilla["bodega"], dia["producto"]) == 1000
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 0
    assert await _saldo(sesion, semilla["vendedor"]) == Decimal("0.00")


async def test_eliminar_la_carga_pedida_rechaza_su_solicitud(
    cliente, sesion, semilla, dia  # noqa: F811
):
    corte_id = await _corte(sesion, dia, semilla)
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    cab = await _cab(cliente, "GER01")
    await _cerrar(cliente, corte_id, cab)
    folio = (await _aceptar(cliente, sid, cab)).json()["solicitud"]["carga_folio"]
    carga = (await _uno(sesion, "SELECT id FROM cargas WHERE folio = :f", f=folio))["id"]
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 300

    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/carga/{carga}"))
    assert "queda rechazada con tu motivo" in pagina
    await _anular(cliente, "carga", carga)
    solicitud = await _uno(sesion, "SELECT estado, motivo FROM solicitudes_carga WHERE id = :s",
                           s=sid)
    assert solicitud["estado"] == "rechazada"
    assert solicitud["motivo"] == f"Se anuló la carga {folio}: {MOTIVO}"
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 60
    # Ese día ya se le puede aceptar otra.
    otra = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    assert (await _aceptar(cliente, otra, cab)).status_code == 200


# ---------------------------------------------------------------------------
# Lo que quedaría en negativo
# ---------------------------------------------------------------------------


async def test_lo_que_quedaria_en_negativo_pide_la_casilla(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _entrar(cliente)
    # La carga de 240, con 180 ya vendidas: el camión solo tiene 60.
    pagina = await cliente.get(f"/panel/operaciones/carga/{dia['carga']}")
    assert 'id="aunque_quede_en_negativo"' in pagina.text
    r = await _anular(cliente, "carga", dia["carga"])
    assert "Quedaría en negativo" in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 60

    r = await _anular(cliente, "carga", dia["carga"], negativo=True)
    assert "Carga CG-TEST anulada." in solo_texto(r)
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == -180
    datos = (await _uno(sesion, "SELECT datos FROM anulaciones WHERE documento_id = :c",
                        c=dia["carga"]))["datos"]
    assert datos["en_negativo"] and "(hay 60)" in datos["en_negativo"][0]
    r = await cliente.get(f"/panel/operaciones/carga/{dia['carga']}")
    assert 'id="quedo_en_negativo"' in r.text


# ---------------------------------------------------------------------------
# Varias a la vez
# ---------------------------------------------------------------------------


async def test_eliminar_varias_en_orden_y_salen_de_la_lista(
    cliente, sesion, semilla, dia  # noqa: F811
):
    await _entrar(cliente)
    lista = await cliente.get("/panel/operaciones?periodo=todo")
    assert f'id="marcar_{dia["venta"]}"' in lista.text
    assert 'id="boton_eliminar_marcadas"' in lista.text
    # «Marcar todas» las devuelve marcadas, sin JavaScript.
    todas = await cliente.get("/panel/operaciones?periodo=todo&marcar=todas")
    casilla = re.search(rf'<input[^>]*id="marcar_{dia["carga"]}"[^>]*>', todas.text).group(0)
    assert "checked" in casilla
    assert "checked" not in re.search(
        rf'<input[^>]*id="marcar_{dia["carga"]}"[^>]*>', lista.text
    ).group(0)

    # Se marcan la carga y la venta, en ese orden: se eliminan al revés.
    marcadas = [f"carga:{dia['carga']}", f"venta:{dia['venta']}"]
    revision = await cliente.post(
        "/panel/operaciones/eliminar",
        data={"csrf": csrf_del_panel(cliente, lista), "op": marcadas},
    )
    assert revision.status_code == 200
    plano = solo_texto(revision)
    assert "Eliminar 2 operación(es)" in plano
    assert plano.index("VEND01-000001") < plano.index("CG-TEST")

    # Sin motivo no se toca nada.
    r = await cliente.post(
        "/panel/operaciones/eliminar/confirmar",
        data={"csrf": csrf_del_panel(cliente, revision), "op": marcadas, "motivo": "no"},
    )
    assert "al menos 10 letras" in solo_texto(r)
    assert (await sesion.execute(text("SELECT count(*) FROM anulaciones"))).scalar_one() == 0

    r = await cliente.post(
        "/panel/operaciones/eliminar/confirmar",
        data={"csrf": csrf_del_panel(cliente, revision), "op": marcadas,
              "motivo": "eran pruebas del almacén"},
    )
    assert "Eliminadas: 2 de 2" in solo_texto(r)
    # La venta regresó las 180 antes de que la carga se llevara las 240.
    assert await _existencia(sesion, semilla["camion"], dia["producto"]) == 0
    assert await _existencia(sesion, semilla["bodega"], dia["producto"]) == 1000

    # Ya no salen en la lista de siempre; sí en Eliminadas.
    r = await cliente.get("/panel/operaciones?periodo=todo")
    assert f'id="op_{dia["venta"]}"' not in r.text and f'id="op_{dia["carga"]}"' not in r.text
    r = await cliente.get("/panel/operaciones?periodo=todo&estado=anuladas")
    assert f'id="op_{dia["venta"]}"' in r.text and f'id="op_{dia["carga"]}"' in r.text

    # Otra vez las mismas: no se puede, y dice por qué.
    r = await cliente.post(
        "/panel/operaciones/eliminar/confirmar",
        data={"csrf": csrf_del_panel(cliente, revision), "op": marcadas,
              "motivo": "eran pruebas del almacén"},
    )
    plano = solo_texto(r)
    assert "Eliminadas: 0 de 2" in plano and "Ya se anuló" in plano


# ---------------------------------------------------------------------------
# La compra pagada
# ---------------------------------------------------------------------------


async def test_la_compra_pagada_se_elimina_con_sus_pagos(
    cliente, sesion, semilla, dia  # noqa: F811
):
    from app.api.admin.entradas import (
        abrir_entrada,
        agregar_renglon_a_entrada,
        confirmar_entrada,
    )

    proveedor = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO proveedores (id, codigo, nombre) VALUES (:p, 'COMP01', 'El Competidor')"),
        {"p": proveedor},
    )
    await sesion.commit()
    entrada = await abrir_entrada(
        sesion, almacen_destino_id=str(semilla["bodega"]), motivo="compra",
        proveedor_id=str(proveedor), referencia="F-9", quien=semilla["admin"],
    )
    await agregar_renglon_a_entrada(
        sesion, entrada, producto="ATUN-140", unidad_codigo="PZA", cantidad="10", costo="5"
    )
    await confirmar_entrada(sesion, entrada, quien=semilla["admin"])
    await sesion.execute(
        text("INSERT INTO pagos_proveedor (entrada_id, importe, registrado_por) "
             "VALUES (:e, 20, :q)"),
        {"e": entrada, "q": semilla["admin"]},
    )
    await sesion.commit()

    await _entrar(cliente)
    pagina = solo_texto(await cliente.get(f"/panel/operaciones/entrada/{entrada}"))
    assert "Y con ella los $20.00 que ya se le habían pagado" in pagina
    r = await _anular(cliente, "entrada", entrada)
    assert "anulada" in solo_texto(r)
    pagos = (
        await sesion.execute(text("SELECT count(*) FROM pagos_proveedor WHERE entrada_id = :e"),
                             {"e": entrada})
    ).scalar_one()
    assert pagos == 0
    assert await _existencia(sesion, semilla["bodega"], dia["producto"]) == 760
