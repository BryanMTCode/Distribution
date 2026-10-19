"""El corte del vendedor se cierra solo (ADR 0002 §133).

«El corte será automático, no tendrá que ser validado por el supervisor: solo lo
hará el vendedor.»

1. **Al llegar el corte se encola su cierre**, y el worker lo cierra a nombre
   del vendedor con el efectivo que calculó su teléfono: la liquidación queda
   cerrada y el camión en lo calculado, sin que nadie de la oficina lo toque.
2. **Con el teléfono atrasado no se cierra**: se queda por cerrar con el motivo
   escrito, se reintenta, y se cierra en cuanto el teléfono reporta su cola en
   cero —por un lote de sincronización de verdad—.
3. **La oficina ve el motivo** del que no se cerró solo, y sigue pudiendo
   cerrarlo a mano.
4. **Después, su carga de mañana se acepta** sin esperar a nadie.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.sync.sobres import Operacion
from tests.ayudas_sync import sobre
from tests.test_cierre_del_vendedor import _aceptar, _corte, _ctx, _solicitud, dia  # noqa: F401
from tests.test_cortes_api import _cab
from tests.test_panel_liquidacion import _entrar, _reportar_cola

pytestmark = pytest.mark.asyncio


async def _correr_el_cierre(corte_id, intento: int = 0) -> None:
    from app.workers.principal import MANEJADORES

    await MANEJADORES["cerrar_corte_del_vendedor"]({"corte_id": str(corte_id), "intento": intento})


async def _corte_en_la_base(sesion, corte_id) -> dict:
    await sesion.rollback()
    return dict(
        (
            await sesion.execute(text("SELECT * FROM cortes_vendedor WHERE id = :c"),
                                 {"c": corte_id})
        ).mappings().one()
    )


async def _cierres_encolados(sesion, corte_id) -> list[str]:
    return list(
        (
            await sesion.execute(
                text("SELECT clave_unica FROM jobs WHERE tipo = 'cerrar_corte_del_vendedor' "
                     "   AND payload->>'corte_id' = :c AND estado = 'pendiente' ORDER BY id"),
                {"c": str(corte_id)},
            )
        ).scalars()
    )


async def test_al_llegar_se_encola_y_el_worker_lo_cierra_a_nombre_del_vendedor(
    sesion, semilla, dia  # noqa: F811
):
    # El teléfono manda lo vendido en efectivo, que es lo que calculó.
    corte_id = await _corte(sesion, dia, semilla, efectivo="2250.00")
    assert await _cierres_encolados(sesion, corte_id) == [f"cerrar_corte:{corte_id}"]

    await _correr_el_cierre(corte_id)
    corte = await _corte_en_la_base(sesion, corte_id)
    assert corte["estado"] == "cerrado"
    assert corte["resuelto_por"] == semilla["vendedor"]
    assert corte["efectivo_recibido"] == Decimal("2250.00")
    assert corte["no_se_cerro_solo"] is None
    liquidacion = (
        await sesion.execute(text("SELECT estado, efectivo_entregado, cerrada_por "
                                  "  FROM liquidaciones WHERE id = :l"),
                             {"l": corte["liquidacion_id"]})
    ).mappings().one()
    assert liquidacion["estado"] == "cerrada"
    assert liquidacion["efectivo_entregado"] == Decimal("2250.00")
    assert liquidacion["cerrada_por"] == semilla["vendedor"]
    # Cuadra: nada a su cuenta.
    cargos = (
        await sesion.execute(
            text("SELECT count(*) FROM cuenta_vendedor WHERE liquidacion_id = :l"),
            {"l": corte["liquidacion_id"]},
        )
    ).scalar_one()
    assert cargos == 0
    # Correrlo otra vez no hace nada.
    await _correr_el_cierre(corte_id)
    assert (await _corte_en_la_base(sesion, corte_id))["estado"] == "cerrado"


async def test_con_el_telefono_atrasado_espera_y_se_cierra_al_terminar_de_subir(
    sesion, semilla, dia  # noqa: F811
):
    corte_id = await _corte(sesion, dia, semilla, efectivo="2250.00")
    await _reportar_cola(sesion, dia["dispositivo"], 2)
    await sesion.commit()

    await _correr_el_cierre(corte_id)
    corte = await _corte_en_la_base(sesion, corte_id)
    assert corte["estado"] == "pendiente"
    assert "sin subir" in corte["no_se_cerro_solo"]
    # Se reintenta solo, más tarde.
    assert f"cerrar_corte:{corte_id}:1" in await _cierres_encolados(sesion, corte_id)

    # El teléfono sube lo que le faltaba y reporta su cola en cero: un lote de
    # verdad, con una tienda nueva.
    from app.infra.sync.ingesta import procesar_lote

    await sesion.execute(text("DELETE FROM jobs WHERE tipo = 'cerrar_corte_del_vendedor'"))
    await sesion.commit()
    alta = Operacion(tipo="cliente.crear", entidad_id=uuid.uuid4(),
                     datos={"nombre_comercial": "Abarrotes La Nueva"})
    await procesar_lote(sesion, _ctx(dia, semilla), uuid.uuid4(), [sobre(alta)],
                        cola_pendiente=0)
    assert await _cierres_encolados(sesion, corte_id) == [f"cerrar_corte:{corte_id}"]

    await _correr_el_cierre(corte_id)
    corte = await _corte_en_la_base(sesion, corte_id)
    assert corte["estado"] == "cerrado"
    assert corte["no_se_cerro_solo"] is None


async def test_la_oficina_ve_por_que_no_se_cerro_y_puede_cerrarlo_a_mano(
    cliente, sesion, semilla, dia  # noqa: F811
):
    corte_id = await _corte(sesion, dia, semilla, efectivo="2250.00")
    await _reportar_cola(sesion, dia["dispositivo"], 2)
    await sesion.commit()
    await _correr_el_cierre(corte_id, intento=99)   # sin más reintentos

    cab = await _cab(cliente, "GER01")
    r = await cliente.get("/v1/cierres/cortes", headers=cab)
    assert r.status_code == 200
    pendientes = [c["corte"] for c in r.json()["pendientes"] if c["corte"]]
    assert pendientes and "sin subir" in pendientes[0]["no_se_cerro_solo"]

    await _entrar(cliente)
    panel = await cliente.get("/panel/cierres")
    assert "No se cerró solo" in panel.text and "sin subir" in panel.text

    await _reportar_cola(sesion, dia["dispositivo"], 0)
    await sesion.commit()
    r = await cliente.post(f"/v1/cierres/cortes/{corte_id}/cerrar", headers=cab)
    assert r.status_code == 200, r.text
    assert (await _corte_en_la_base(sesion, corte_id))["estado"] == "cerrado"


async def test_cerrado_solo_su_carga_de_manana_se_acepta(cliente, sesion, semilla, dia):  # noqa: F811
    corte_id = await _corte(sesion, dia, semilla, efectivo="2250.00")
    sid = await _solicitud(sesion, dia, semilla, corte_id=corte_id)
    await _correr_el_cierre(corte_id)
    r = await _aceptar(cliente, sid, await _cab(cliente, "GER01"))
    assert r.status_code == 200, r.text
