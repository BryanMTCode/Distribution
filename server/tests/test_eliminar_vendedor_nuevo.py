"""Un vendedor recién creado se puede eliminar aunque ya haya usado su teléfono.

«Acabo de crear un vendedor pero no lo puedo eliminar porque dice que tiene cosas
firmadas, no entiendo.» Lo que lo detenía no eran documentos: era la tienda que
dio de alta desde su teléfono (apunta al teléfono) o lo que su teléfono mandó y el
servidor rechazó (cuarentena). Y el aviso decía «documentos firmados» sin decir
cuáles.

1. **Se elimina** con su teléfono vinculado aunque haya dado de alta una tienda
   —la tienda se queda, sin quién ni con qué teléfono la capturó—, tenga
   operaciones rechazadas o haya pedido una carga que no se le cargó.
2. **Lo que sí lo impide se nombra**: sus ventas, o los ajustes del inventario
   de su camión.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.domain.sync.sobres import Operacion
from app.infra.ajuste_de_existencia import registrar_ajuste
from app.infra.sync.manejadores import Contexto
from tests.ayudas_sync import sobre
from tests.conftest import csrf_del_panel
from tests.test_panel_equipo import PASSWORD_NUEVA, _crear_usuario, _entrar

pytestmark = pytest.mark.asyncio


async def _vendedor_con_telefono(cliente, sesion) -> dict:
    """VEND02 dado de alta en el panel, con su camión, y su teléfono vinculado
    que ya entró y pidió sus folios."""
    await _entrar(cliente)
    await _crear_usuario(cliente, codigo="VEND02")
    await sesion.rollback()
    usuario, camion = (
        await sesion.execute(text("SELECT id, almacen_id FROM usuarios WHERE codigo = 'VEND02'"))
    ).one()
    telefono = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos (id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d, :u, 'Moto G54', 'activo', now())"),
        {"d": telefono, "u": usuario},
    )
    await sesion.commit()
    r = await cliente.post(
        "/v1/auth/login",
        json={"codigo": "VEND02", "password": PASSWORD_NUEVA, "dispositivo_id": str(telefono)},
    )
    assert r.status_code == 200, r.text
    r = await cliente.post(
        f"/v1/dispositivos/{telefono}/folios",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
    )
    assert r.status_code == 200, r.text
    return {
        "usuario": usuario,
        "camion": camion,
        "contexto": Contexto(dispositivo_id=telefono, usuario_id=usuario, almacen_id=camion),
    }


async def _eliminar(cliente, sesion, usuario) -> str:
    ficha = await cliente.get(f"/panel/equipo/usuarios/{usuario}")
    r = await cliente.post(
        f"/panel/equipo/usuarios/{usuario}/eliminar",
        data={"csrf": csrf_del_panel(cliente, ficha), "confirmo": "1"},
        follow_redirects=True,
    )
    await sesion.rollback()
    avisos = re.findall(r'class="aviso-caja[^"]*"[^>]*>(.*?)</div>', r.text, re.S)
    return " ".join(" ".join(a.split()) for a in avisos)


async def _sigue(sesion, usuario) -> bool:
    return bool(
        (await sesion.execute(text("SELECT count(*) FROM usuarios WHERE id = :u"),
                              {"u": usuario})).scalar_one()
    )


async def test_con_una_tienda_dada_de_alta_desde_su_telefono(cliente, sesion, semilla):
    from app.infra.sync.ingesta import procesar_lote

    v = await _vendedor_con_telefono(cliente, sesion)
    tienda = uuid.uuid4()
    alta = Operacion(tipo="cliente.crear", entidad_id=tienda,
                     datos={"nombre_comercial": "Abarrotes La Prueba"})
    await procesar_lote(sesion, v["contexto"], uuid.uuid4(), [sobre(alta)], cola_pendiente=0)
    await sesion.commit()

    aviso = await _eliminar(cliente, sesion, v["usuario"])
    assert "VEND02 se eliminó" in aviso, aviso
    assert not await _sigue(sesion, v["usuario"])
    # La tienda se queda, sin quién ni con qué teléfono la capturó.
    fila = (
        await sesion.execute(
            text("SELECT creado_por, dispositivo_id FROM clientes WHERE id = :c"), {"c": tienda}
        )
    ).one()
    assert tuple(fila) == (None, None)


async def test_con_operaciones_rechazadas_y_una_carga_pedida(cliente, sesion, semilla):
    from app.infra.sync.ingesta import procesar_lote

    v = await _vendedor_con_telefono(cliente, sesion)
    mala = Operacion(tipo="venta.crear", entidad_id=uuid.uuid4(), datos={"basura": 1})
    await procesar_lote(sesion, v["contexto"], uuid.uuid4(), [sobre(mala)], cola_pendiente=0)
    await sesion.execute(
        text("INSERT INTO solicitudes_carga (id, vendedor_id, dispositivo_id, fecha_operativa, "
             "                               estado, recibido_en) "
             "VALUES (:id, :v, :d, :f, 'pendiente', now())"),
        {"id": uuid.uuid4(), "v": v["usuario"], "d": v["contexto"].dispositivo_id,
         "f": date.today()},
    )
    await sesion.commit()
    assert (await sesion.execute(text("SELECT count(*) FROM sync_cuarentena"))).scalar_one()

    aviso = await _eliminar(cliente, sesion, v["usuario"])
    assert "VEND02 se eliminó" in aviso, aviso
    assert (await sesion.execute(text("SELECT count(*) FROM sync_cuarentena"))).scalar_one() == 0
    assert (await sesion.execute(text("SELECT count(*) FROM solicitudes_carga"))).scalar_one() == 0


async def test_lo_que_si_lo_impide_se_nombra(cliente, sesion, semilla):
    v = await _vendedor_con_telefono(cliente, sesion)
    producto = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva) "
             "VALUES (:p, 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0)"),
        {"p": producto},
    )
    # La oficina le ajustó el camión: es historia del camión, no de él.
    await registrar_ajuste(
        sesion, almacen_id=v["camion"], producto_id=producto, es_camion=True,
        tipo="entrada", actual=Decimal(0), delta=Decimal(5), contado=None,
        nota="le subimos 5 de prueba", quien=semilla["admin"],
    )
    await sesion.commit()

    aviso = await _eliminar(cliente, sesion, v["usuario"])
    assert "no se puede borrar" in aviso
    assert "documentos firmados" not in aviso
    assert "su camión" in aviso, aviso
    assert await _sigue(sesion, v["usuario"])
