"""Reprocesar desde el panel lo que quedó en cuarentena.

Lo reportó la operación en octubre de 2026: el teléfono con la app nueva mandó
algo que el servidor todavía viejo rechazó; se actualizó el servidor, el vendedor
tocó «reintentar»... y la barra siguió roja. El servidor recuerda que rechazó el
sobre y a cada reenvío le contesta lo mismo sin volver a aplicarlo. Y el panel
solo podía «descartar».

Lo que se defiende:

1. Reprocesar aplica el payload guardado con las mismas reglas que el push.
2. Después, el reintento del teléfono recibe «duplicada»: ya está del otro lado,
   lo saca de su cola y se le quita lo rojo. Y no se aplica dos veces.
3. Si la causa sigue, no se aplica nada y la operación se queda con el motivo.
4. Lo que no coincide con su firma no se reprocesa nunca.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.ayudas_sync import a_json, operacion_cliente, sobre
from tests.test_panel_cobranza import _csrf
from tests.test_plan_visita import _entrar
from tests.test_sync_push import _cab_vendedor, _contar

pytestmark = pytest.mark.asyncio


async def _rechazado_por_ruta_ajena(cliente, sesion, semilla):
    """Un alta de cliente en una ruta que el vendedor todavía no tiene."""
    otra_ruta = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO rutas(id, codigo, nombre) VALUES (:r,'R09','Ruta 9')"), {"r": otra_ruta}
    )
    await sesion.commit()
    cab = await _cab_vendedor(cliente, sesion, semilla)
    cuerpo = a_json([sobre(operacion_cliente("Abarrotes Lupita", ruta_id=str(otra_ruta)))])
    r = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert r.json()["rechazadas"] == 1
    id_fila = (await sesion.execute(text("SELECT id FROM sync_cuarentena"))).scalar_one()
    return cab, cuerpo, otra_ruta, id_fila


async def _reprocesar(cliente, id_fila):
    await _entrar(cliente)
    pantalla = await cliente.get(f"/panel/cuarentena/{id_fila}")
    assert 'id="boton_reprocesar"' in pantalla.text
    return await cliente.post(
        f"/panel/cuarentena/{id_fila}/reprocesar",
        data={"csrf": _csrf(cliente, pantalla)},
        follow_redirects=True,
    )


async def test_corregida_la_causa_se_reprocesa_y_el_telefono_se_destraba(
    cliente, sesion, semilla
):
    cab, cuerpo, otra_ruta, id_fila = await _rechazado_por_ruta_ajena(cliente, sesion, semilla)

    # Reintentar sin reprocesar no sirve: es el callejón que se reportó.
    otra = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert otra.json()["resultados"][0]["estado"] == "rechazada"

    # La oficina corrige la causa: le da la ruta al vendedor.
    await sesion.execute(
        text("INSERT INTO usuarios_rutas (usuario_id, ruta_id) VALUES (:u, :r)"),
        {"u": semilla["vendedor"], "r": otra_ruta},
    )
    await sesion.commit()

    r = await _reprocesar(cliente, id_fila)
    assert 'id="aviso_guardado"' in r.text
    assert await _contar(sesion, "clientes") == 1
    estado = (
        await sesion.execute(
            text("SELECT estado FROM sync_cuarentena WHERE id = :id"), {"id": id_fila}
        )
    ).scalar_one()
    assert estado == "reprocesada"

    # El teléfono toca «reintentar»: el servidor ya la tiene.
    reintento = await cliente.post("/v1/sync/push", json=cuerpo, headers=cab)
    assert reintento.json()["resultados"][0]["estado"] == "duplicada"
    assert await _contar(sesion, "clientes") == 1, "se aplicó dos veces"


async def test_si_la_causa_sigue_no_se_aplica_nada(cliente, sesion, semilla):
    _, _, _, id_fila = await _rechazado_por_ruta_ajena(cliente, sesion, semilla)

    r = await _reprocesar(cliente, id_fila)
    assert 'id="aviso_error"' in r.text
    assert "Sigue sin poder aplicarse" in r.text
    assert await _contar(sesion, "clientes") == 0
    estado = (
        await sesion.execute(
            text("SELECT estado FROM sync_cuarentena WHERE id = :id"), {"id": id_fila}
        )
    ).scalar_one()
    assert estado == "pendiente"


async def test_lo_que_no_coincide_con_su_firma_no_se_reprocesa(cliente, sesion, semilla):
    from app.infra.sync.ingesta import reprocesar_cuarentena

    _, _, _, id_fila = await _rechazado_por_ruta_ajena(cliente, sesion, semilla)
    await sesion.execute(
        text("UPDATE sync_cuarentena SET error_codigo = 'hash_no_coincide' WHERE id = :id"),
        {"id": id_fila},
    )
    await sesion.commit()

    paso, mensaje = await reprocesar_cuarentena(sesion, id_fila, semilla["admin"])
    await sesion.rollback()
    assert paso is False
    assert "firma" in mensaje

    await _entrar(cliente)
    assert 'id="boton_reprocesar"' not in (await cliente.get(f"/panel/cuarentena/{id_fila}")).text
