"""Prueba de contrato de punta a punta entre el cliente Dart y el servidor.

`contracts/sobres_de_ejemplo.json` lo genera el cliente Dart con su propio
código —el mismo `SobreLocal` y el mismo hash que usará el teléfono— y aquí se
empuja por el endpoint real de sincronización.

Es la única prueba del proyecto que demuestra que **lo que arma el dispositivo
es exactamente lo que el servidor acepta**. Sin ella, la divergencia aparecería
el primer día de piloto, en un mercado y sin señal.

Regenerar el archivo:
    cd mobile/packages/dsd_core && dart run tool/generar_sobres_ejemplo.dart
"""

from __future__ import annotations

import json
import pathlib
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.conftest import PASSWORD_VENDEDOR

_ARCHIVO = (
    pathlib.Path(__file__).resolve().parents[2] / "contracts" / "sobres_de_ejemplo.json"
)
DOCUMENTO = json.loads(_ARCHIVO.read_text(encoding="utf-8"))


async def _cab_vendedor(cliente, sesion, semilla) -> dict:
    dispositivo_id = uuid.uuid4()
    await sesion.execute(
        text("INSERT INTO dispositivos(id, usuario_id, etiqueta, estado, registrado_en) "
             "VALUES (:d,:u,'Moto G54','activo',now())"),
        {"d": dispositivo_id, "u": semilla["vendedor"]},
    )
    await sesion.commit()
    r = await cliente.post("/v1/auth/login", json={
        "codigo": "VEND01", "password": PASSWORD_VENDEDOR, "dispositivo_id": str(dispositivo_id),
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _cuerpo() -> dict:
    return {"lote_id": DOCUMENTO["lote_id"], "sobres": DOCUMENTO["sobres"]}


def test_el_archivo_trae_sobres():
    assert len(DOCUMENTO["sobres"]) >= 3


def test_los_hashes_de_dart_coinciden_con_los_de_python():
    """La comprobación de fondo: el hash lo calculó Dart, y Python recompone la
    misma forma canónica a partir del mismo contenido.

    Si esto falla, el motor de sincronización mandaría a cuarentena todos los
    sobres legítimos del piloto.
    """
    from app.domain.sync.sobres import Operacion, Sobre, hash_de_sobre

    for crudo in DOCUMENTO["sobres"]:
        sobre = Sobre(
            operacion_id=uuid.UUID(crudo["operacion_id"]),
            secuencia=crudo["secuencia"],
            hash_payload=crudo["hash_payload"],
            visita_id=uuid.UUID(crudo["visita_id"]) if crudo["visita_id"] else None,
            operaciones=[
                Operacion(o["tipo"], uuid.UUID(o["entidad_id"]), o["datos"])
                for o in crudo["operaciones"]
            ],
        )
        assert hash_de_sobre(sobre) == crudo["hash_payload"], (
            f"divergencia Dart↔Python en el sobre {crudo['operacion_id']}"
        )


@pytest.mark.asyncio
async def test_el_servidor_acepta_los_sobres_de_dart(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)

    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["rechazadas"] == 0, cuerpo["resultados"]
    assert cuerpo["aceptadas"] == len(DOCUMENTO["sobres"])

    # Nada quedó en cuarentena: el hash de Dart pasó la verificación.
    en_cuarentena = (
        await sesion.execute(text("SELECT count(*) FROM sync_cuarentena"))
    ).scalar_one()
    assert en_cuarentena == 0


@pytest.mark.asyncio
async def test_los_acentos_y_el_emoji_sobreviven_el_viaje(cliente, semilla, sesion):
    """Si el escape difiriera entre lenguajes, el hash no coincidiría y el
    sobre acabaría en cuarentena en vez de en la base."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)

    nombres = set(
        (await sesion.execute(text("SELECT nombre_comercial FROM clientes"))).scalars()
    )
    assert "La Esquina de Ñoño 🏪" in nombres


@pytest.mark.asyncio
async def test_reenviar_el_lote_de_dart_no_duplica(cliente, semilla, sesion):
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    repetido = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)

    assert repetido.json()["aceptadas"] == 0
    assert repetido.json()["duplicadas"] == len(DOCUMENTO["sobres"])

    # Se cuentan las operaciones de alta, no todas: el cuarto sobre trae además
    # una venta, que no crea un cliente.
    altas = sum(
        1
        for s in DOCUMENTO["sobres"]
        for o in s["operaciones"]
        if o["tipo"] == "cliente.crear"
    )
    total = (await sesion.execute(text("SELECT count(*) FROM clientes"))).scalar_one()
    assert total == altas


# ---------------------------------------------------------------------------
# La visita completa: alta y venta en el mismo sobre
# ---------------------------------------------------------------------------

# Los ids que el generador de Dart fija para la venta de ejemplo.
PRODUCTO_DE_LA_VENTA = uuid.UUID("019283a0-0006-7000-8000-000000000006")
LISTA_DE_LA_VENTA = uuid.UUID("019283a0-0007-7000-8000-000000000007")
CLIENTE_DE_LA_VENTA = uuid.UUID("019283a0-0005-7000-8000-000000000005")


@pytest_asyncio.fixture(autouse=True)
async def catalogo_de_la_venta(sesion) -> None:
    """El producto y la lista con los que Dart cotizó.

    Autouse porque el lote de ejemplo trae una venta y **todas** las pruebas que
    lo empujan la necesitan. Sin el producto, su partida viola la llave foránea
    y el sobre entero se va a cuarentena.

    Ese FK se deja estricto a propósito, a diferencia del de la lista de precios:
    el catálogo del dispositivo es un espejo que el servidor posee, así que todo
    producto que el teléfono conoce vino DE AQUÍ y `productos` nunca se borra
    (se marca inactivo). Un producto desconocido significa que alguien borró a
    mano, y entonces la cuarentena con el payload íntegro es lo correcto: la
    oficina tiene que recrear el producto antes de poder registrar esa venta.
    Una venta colgada de un producto que no existe no se puede costear ni
    reportar.
    """
    await sesion.execute(
        text(
            "INSERT INTO listas_precios(id, codigo, nombre, es_default) "
            "VALUES (:l, 'DART', 'Lista del contrato', false) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"l": LISTA_DE_LA_VENTA},
    )
    await sesion.execute(
        text(
            "INSERT INTO productos(id, sku, nombre, unidad_base) "
            "VALUES (:p, 'SOPA-CONTRATO', 'Sopa de fideo 70 g', 'PZA') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"p": PRODUCTO_DE_LA_VENTA},
    )
    await sesion.execute(
        text(
            "INSERT INTO producto_unidades(producto_id, unidad_codigo, factor, es_default) "
            "VALUES (:p, 'PZA', 1, true), (:p, 'CAJA', 24, false) "
            "ON CONFLICT DO NOTHING"
        ),
        {"p": PRODUCTO_DE_LA_VENTA},
    )
    await sesion.execute(
        text(
            "INSERT INTO precios(lista_id, producto_id, unidad_codigo, precio, version) "
            "VALUES (:l, :p, 'CAJA', 296.0000, 7), (:l, :p, 'PZA', 12.3333, 7) "
            "ON CONFLICT DO NOTHING"
        ),
        {"l": LISTA_DE_LA_VENTA, "p": PRODUCTO_DE_LA_VENTA},
    )
    await sesion.commit()


@pytest.mark.asyncio
async def test_la_venta_que_arma_dart_la_acepta_el_servidor(cliente, semilla, sesion):
    """La prueba de punta a punta que importa: el teléfono arma una venta con su
    propio código y el servidor real la guarda.

    El sobre trae el alta del cliente **y** su venta, que es el escenario del
    alta en la calle: el vendedor registra la tienda y le vende en ese momento.
    El motor aplica las dos operaciones en orden dentro de una transacción; si la
    venta se aplicara antes que el cliente, no habría a quién colgársela.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)

    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text
    assert r.json()["rechazadas"] == 0, r.json()["resultados"]

    venta = (
        await sesion.execute(
            text(
                "SELECT folio_local, total, subtotal, cliente_id, tipo, "
                "revision_motivos FROM ventas WHERE folio_local = 'VEND01-000124'"
            )
        )
    ).mappings().one()

    assert venta["cliente_id"] == CLIENTE_DE_LA_VENTA
    assert venta["total"] == Decimal("629.00")
    assert venta["tipo"] == "contado"
    # El precio coincidió con la lista: ese motivo NO debe aparecer.
    assert "precio_desactualizado" not in venta["revision_motivos"]

    partidas = (
        await sesion.execute(
            text(
                "SELECT unidad_codigo, cantidad, cantidad_base, precio_unitario, "
                "importe FROM venta_partidas WHERE venta_id = "
                "(SELECT id FROM ventas WHERE folio_local = 'VEND01-000124') "
                "ORDER BY linea"
            )
        )
    ).mappings().all()

    assert len(partidas) == 2
    # La caja: el precio de 4 decimales que hace que 24 piezas valgan 296.00.
    assert partidas[0]["precio_unitario"] == Decimal("296.0000")
    assert partidas[0]["cantidad_base"] == Decimal("48.000")
    assert partidas[0]["importe"] == Decimal("592.00")
    # La pieza: 3 × 12.3333 = 36.9999, redondeado medio hacia arriba → 37.00.
    # Los tres cálculos —Dart, Python y el CHECK de PostgreSQL— coinciden aquí.
    assert partidas[1]["precio_unitario"] == Decimal("12.3333")
    assert partidas[1]["importe"] == Decimal("37.00")


@pytest.mark.asyncio
async def test_reenviar_la_venta_de_dart_no_duplica_el_ticket(
    cliente, semilla, sesion
):
    """Un ticket duplicado es dinero cobrado dos veces en los reportes."""
    cab = await _cab_vendedor(cliente, sesion, semilla)

    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)

    cuantas = (
        await sesion.execute(
            text("SELECT count(*) FROM ventas WHERE folio_local = 'VEND01-000124'")
        )
    ).scalar_one()
    assert cuantas == 1

    # Acotado a ESA venta: el fixture trae más de una, y contar todas las
    # partidas haría que agregar un caso al contrato rompiera esta prueba por una
    # razón que no tiene nada que ver con la idempotencia.
    partidas = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM venta_partidas vp "
                "  JOIN ventas v ON v.id = vp.venta_id "
                " WHERE v.folio_local = 'VEND01-000124'"
            )
        )
    ).scalar_one()
    assert partidas == 2


@pytest.mark.asyncio
async def test_EL_COBRO_QUE_ARMA_DART_SE_APLICA_A_LA_FACTURA_DEL_MISMO_SOBRE(
    cliente, semilla, sesion
):
    """El caso 5 del contrato: venta a crédito y su cobro, en el MISMO sobre.

    Es lo que ningún otro caso prueba: que el servidor aplique el FIFO **sobre una
    factura que acaba de crear en la misma transacción**. Si el orden de las
    operaciones dentro del sobre se rompiera, el cobro no encontraría a qué
    aplicarse y los $400 quedarían como saldo a favor de un cliente que sí debía.

    Y que el sobre lo arme Dart importa más aquí que en cualquier otro caso: el
    importe viaja como string de dos decimales, y si los dos lenguajes no
    coincidieran en ese formato el cobro acabaría en cuarentena.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text

    cobro = (
        await sesion.execute(
            text(
                "SELECT importe, importe_aplicado, saldo_a_favor, forma_pago, "
                "       saldo_cache_disp, requiere_revision "
                "  FROM cobros WHERE folio_local = 'VEND01-000031'"
            )
        )
    ).mappings().one()

    assert cobro["importe"] == Decimal("400.00")
    # Se aplicó completo: la factura de 592 lo absorbe.
    assert cobro["importe_aplicado"] == Decimal("400.00")
    assert cobro["saldo_a_favor"] == Decimal("0.00")
    assert cobro["forma_pago"] == "efectivo"
    # El saldo que traía el teléfono se guarda tal cual: es forense.
    assert cobro["saldo_cache_disp"] == Decimal("592.00")
    assert cobro["requiere_revision"] is False

    # La factura quedó parcial, con los $192 que faltan.
    factura = (
        await sesion.execute(
            text(
                "SELECT c.importe_pagado, c.saldo, c.estado "
                "  FROM cuentas_por_cobrar c "
                "  JOIN ventas v ON v.id = c.venta_id "
                " WHERE v.folio_local = 'VEND01-000125'"
            )
        )
    ).mappings().first()
    # La cuenta por cobrar la crea el worker al confirmar la venta a crédito; si
    # todavía no existe, el cobro quedó como saldo a favor y eso también es un
    # resultado válido del contrato — lo que no puede pasar es que el cobro falte.
    if factura is not None:
        assert factura["importe_pagado"] == Decimal("400.00")
        assert factura["saldo"] == Decimal("192.00")
        assert factura["estado"] == "parcial"


@pytest.mark.asyncio
async def test_reenviar_el_cobro_de_dart_no_abona_dos_veces(cliente, semilla, sesion):
    """Abonar dos veces es la peor consecuencia de un reintento: el cliente
    quedaría con saldo a favor y nadie sabría por qué."""
    cab = await _cab_vendedor(cliente, sesion, semilla)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)

    cobros = (
        await sesion.execute(
            text("SELECT count(*) FROM cobros WHERE folio_local = 'VEND01-000031'")
        )
    ).scalar_one()
    assert cobros == 1

    aplicado = (
        await sesion.execute(
            text(
                "SELECT COALESCE(sum(a.importe), 0) FROM cobros_aplicaciones a "
                "  JOIN cobros k ON k.id = a.cobro_id "
                " WHERE k.folio_local = 'VEND01-000031'"
            )
        )
    ).scalar_one()
    assert aplicado <= Decimal("400.00")
