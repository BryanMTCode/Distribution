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
                "SELECT folio_local, total, subtotal, cliente_id, tipo, forma_pago, "
                "pago_estado, revision_motivos FROM ventas "
                "WHERE folio_local = 'VEND01-000124'"
            )
        )
    ).mappings().one()

    assert venta["cliente_id"] == CLIENTE_DE_LA_VENTA
    assert venta["total"] == Decimal("629.00")
    assert venta["tipo"] == "contado"
    assert (venta["forma_pago"], venta["pago_estado"]) == ("efectivo", "confirmado")
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
async def test_LA_TRANSFERENCIA_QUE_ARMA_DART_QUEDA_POR_CONFIRMAR(cliente, semilla, sesion):
    """El caso 5 del contrato: una venta pagada en el acto por transferencia.

    Todo es de contado (ADR 0002 §81). Si el nombre del campo divergiera entre
    los dos lenguajes, la venta entraría como efectivo y el corte le pediría al
    vendedor un dinero que no trae. Y la referencia es con lo que la oficina la
    busca en el banco.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text

    venta = (
        await sesion.execute(
            text(
                "SELECT tipo, forma_pago, pago_estado, referencia_pago, total "
                "  FROM ventas WHERE folio_local = 'VEND01-000125'"
            )
        )
    ).mappings().one()
    assert dict(venta) == {
        "tipo": "contado",
        "forma_pago": "transferencia",
        "pago_estado": "por_confirmar",
        "referencia_pago": "SPEI 4471",
        "total": Decimal("592.00"),
    }
    deudas = (
        await sesion.execute(text("SELECT count(*) FROM cuentas_por_cobrar"))
    ).scalar_one()
    assert deudas == 0


@pytest.mark.asyncio
async def test_la_merma_que_arma_dart_sale_del_camion(cliente, semilla, sesion):
    """La merma del teléfono tiene que llegar con su signo y su detalle.

    Es el documento más fácil de romper sin que se note: no hay un total que no
    cuadre, así que una divergencia de contrato no se ve en ningún número. Lo que
    se ve es más tarde y peor — la merma cae en cuarentena, la pérdida queda sin
    explicar, y en la liquidación aparece como **faltante del vendedor**.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text

    merma = (
        await sesion.execute(
            text(
                "SELECT tipo, motivo_codigo, almacen_id, observaciones "
                "  FROM mermas WHERE folio_local = 'VEND01-000007'"
            )
        )
    ).mappings().one()
    assert merma["tipo"] == "merma"
    assert merma["motivo_codigo"] == "ROTO"
    # El almacén sale del CONTEXTO, no del payload: Dart lo manda en null a
    # propósito, y dejar que lo declarara permitiría mermar el camión de otro.
    assert merma["almacen_id"] == semilla["camion"]
    assert merma["observaciones"] == "Se cayó la tarima al frenar"

    # Las dos cajas viajan como 48 piezas: la conversión la hace el teléfono y lo
    # que cruza es SIEMPRE unidad base.
    renglon = (
        await sesion.execute(
            text(
                "SELECT d.cantidad_base FROM merma_detalle d "
                "  JOIN mermas m ON m.id = d.merma_id "
                " WHERE m.folio_local = 'VEND01-000007'"
            )
        )
    ).scalar_one()
    assert renglon == Decimal("48.000")

    # Y el movimiento sale del camión. Equivocar el signo produce un descuadre
    # del doble del tamaño de la operación.
    movimiento = (
        await sesion.execute(
            text(
                "SELECT i.tipo, i.almacen_origen_id FROM movimientos_inventario i "
                "  JOIN mermas m ON m.id = i.documento_id "
                " WHERE m.folio_local = 'VEND01-000007'"
            )
        )
    ).mappings().one()
    assert movimiento["tipo"] == "merma"
    assert movimiento["almacen_origen_id"] == semilla["camion"]


@pytest.mark.asyncio
async def test_el_no_drop_que_arma_dart_llega_con_su_geosello(
    cliente, semilla, sesion
):
    """Es el único documento que el servidor rechaza sin ubicación.

    Esta prueba fija que el cliente Dart siempre la manda: si un día dejara de
    hacerlo, todas las visitas perdidas del día se irían a cuarentena y el reporte
    de efectividad quedaría mostrando un vendedor que no visitó a nadie.
    """
    cab = await _cab_vendedor(cliente, sesion, semilla)
    r = await cliente.post("/v1/sync/push", json=_cuerpo(), headers=cab)
    assert r.status_code == 200, r.text

    fila = (
        await sesion.execute(
            text(
                "SELECT motivo_codigo, nota, lat, lng, ubicacion_precision_m, "
                "       vendedor_id, requiere_revision "
                "  FROM no_drops WHERE folio_consecutivo = 12"
            )
        )
    ).mappings().one()
    assert fila["motivo_codigo"] == "AGOTADO_EN_CAMION"
    assert fila["nota"] == "Pidió la presentación de 2 litros"
    assert fila["lat"] == Decimal("19.4330000")
    assert fila["lng"] == Decimal("-99.1340000")
    assert fila["ubicacion_precision_m"] == Decimal("8.00")
    # El vendedor sale del contexto, no del payload.
    assert fila["vendedor_id"] == semilla["vendedor"]
    # Trae su nota, así que no hay nada que revisar.
    assert fila["requiere_revision"] is False
