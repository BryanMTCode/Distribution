"""Ventas de mayoreo de la oficina (ADR 0002 §105).

«El administrador puede hacer ventas a mayoreo al precio que él decida, y los
clientes él los maneja.»

  · **Los clientes** son de la oficina: `clientes.mayoreo = true`, sin ruta, y
    por eso no viajan a ningún teléfono. Se dan de alta aquí mismo.
  · **La venta** sale de una bodega, al precio que se escribió en cada renglón
    —el de la lista general se guarda al lado, para ver el descuento—, de
    contado, con folio MY-. No toca `ventas`: ésa es la del teléfono, con su
    camión y su corte, y una venta de la oficina ahí aparecería en el corte de
    un vendedor que no la hizo.
  · **La existencia** se revisa con candado: no se vende lo que la bodega no
    tiene. Un almacén sí puede quedar negativo por un hecho que llegó tarde,
    pero una captura de la oficina que lo empuja abajo de cero es un dedazo.
  · **Cancelar** devuelve la mercancía a la bodega y pide el motivo.

Lo usan el panel (`api/admin/mayoreo.py`) y la app (`api/v1/mayoreo.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text

PERMISO = "mayoreo.vender"
FORMAS_DE_PAGO = ("efectivo", "transferencia")
RENGLONES_MAXIMOS = 200


class MayoreoInvalido(Exception):
    """No se guardó nada, y el mensaje dice por qué."""


@dataclass(frozen=True)
class RenglonPedido:
    producto_id: uuid.UUID
    unidad_codigo: str
    bultos: Decimal
    precio: Decimal


def _dos(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------


async def clientes_de_mayoreo(sesion) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    """
                    SELECT c.id, c.codigo, c.nombre_comercial, c.telefono,
                           concat_ws(' ', c.calle, c.numero, c.colonia) AS direccion,
                           COALESCE(v.ventas, 0) AS ventas,
                           COALESCE(v.vendido, 0) AS vendido,
                           v.ultima
                      FROM clientes c
                      LEFT JOIN LATERAL (
                            SELECT count(*) AS ventas, sum(total) AS vendido,
                                   max(fecha) AS ultima
                              FROM ventas_mayoreo
                             WHERE cliente_id = c.id AND estado = 'confirmada'
                      ) v ON true
                     WHERE c.mayoreo AND c.estatus <> 'baja'
                     ORDER BY c.nombre_comercial
                    """
                )
            )
        ).mappings().all()
    ]


async def alta_de_cliente(
    sesion,
    *,
    nombre: str,
    telefono: str | None = None,
    calle: str | None = None,
    colonia: str | None = None,
    referencias: str | None = None,
    quien: uuid.UUID | None,
) -> dict:
    """Un cliente de mayoreo nuevo, con su código. No hace commit."""
    limpio = " ".join((nombre or "").split())[:200]
    if not limpio:
        raise MayoreoInvalido("Escribe el nombre del cliente.")
    ya = (
        await sesion.execute(
            text(
                "SELECT codigo FROM clientes WHERE mayoreo AND estatus <> 'baja' "
                "   AND lower(nombre_comercial) = lower(:n)"
            ),
            {"n": limpio},
        )
    ).first()
    if ya is not None:
        raise MayoreoInvalido(f"Ya hay un cliente de mayoreo «{limpio}».")
    consecutivo = (await sesion.execute(text("SELECT nextval('seq_codigo_cliente')"))).scalar_one()
    nuevo = {"id": uuid.uuid4(), "codigo": f"C{consecutivo:05d}", "nombre_comercial": limpio}
    await sesion.execute(
        text(
            """
            INSERT INTO clientes (id, codigo, nombre_comercial, telefono, calle, colonia,
                                  referencias, estatus, origen_alta, mayoreo, creado_por,
                                  lista_precios_id, creado_en, actualizado_en)
            VALUES (:id, :codigo, :nombre, :tel, :calle, :colonia, :refs, 'activo',
                    'oficina', true, :quien,
                    (SELECT id FROM listas_precios WHERE activo
                      ORDER BY es_default DESC, vigente_desde LIMIT 1),
                    now(), now())
            """
        ),
        {
            "id": nuevo["id"], "codigo": nuevo["codigo"], "nombre": limpio,
            "tel": (telefono or "").strip()[:40] or None,
            "calle": (calle or "").strip()[:200] or None,
            "colonia": (colonia or "").strip()[:120] or None,
            "refs": (referencias or "").strip()[:400] or None,
            "quien": quien,
        },
    )
    return nuevo


# ---------------------------------------------------------------------------
# Lo que se puede vender
# ---------------------------------------------------------------------------


async def bodegas(sesion) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    "SELECT id, codigo, nombre FROM almacenes "
                    " WHERE tipo = 'bodega' AND activo ORDER BY codigo = 'BODEGA_PRINCIPAL' DESC, "
                    "       nombre"
                )
            )
        ).mappings().all()
    ]


async def surtido(sesion, almacen_id) -> list[dict]:
    """Lo que hay en la bodega, por familia, con sus presentaciones y el precio de
    lista de cada una."""
    filas = (
        await sesion.execute(
            text(
                """
                SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_base,
                       f.nombre AS familia, e.cantidad AS existencia,
                       u.unidad_codigo, u.factor, pr.precio AS precio_lista
                  FROM existencias e
                  JOIN productos p ON p.id = e.producto_id AND p.activo
                  JOIN producto_unidades u ON u.producto_id = p.id AND u.activo
                  LEFT JOIN categorias f ON f.id = p.categoria_id
                  LEFT JOIN precios pr ON pr.producto_id = p.id
                                      AND pr.unidad_codigo = u.unidad_codigo
                                      AND pr.lista_id = (
                                            SELECT id FROM listas_precios WHERE activo
                                             ORDER BY es_default DESC, vigente_desde LIMIT 1)
                 WHERE e.almacen_id = :a AND e.cantidad > 0
                 ORDER BY (f.id IS NULL), f.orden, f.nombre, p.nombre, u.factor
                """
            ),
            {"a": almacen_id},
        )
    ).mappings().all()
    productos: dict[uuid.UUID, dict] = {}
    for f in filas:
        p = productos.setdefault(
            f["producto_id"],
            {
                "producto_id": f["producto_id"], "sku": f["sku"], "nombre": f["nombre"],
                "unidad_base": f["unidad_base"], "familia": f["familia"],
                "existencia": f["existencia"], "presentaciones": [],
            },
        )
        p["presentaciones"].append(
            {"unidad": f["unidad_codigo"], "factor": f["factor"], "precio_lista": f["precio_lista"]}
        )
    return list(productos.values())


# ---------------------------------------------------------------------------
# Vender
# ---------------------------------------------------------------------------


async def vender(
    sesion,
    *,
    cliente_id: uuid.UUID,
    almacen_id: uuid.UUID,
    renglones: list[RenglonPedido],
    forma_pago: str,
    referencia_pago: str | None,
    nota: str | None,
    quien: uuid.UUID | None,
    fecha: date | None = None,
) -> tuple[uuid.UUID, str]:
    """Registra la venta y saca la mercancía de la bodega. Devuelve (id, folio).
    No hace commit: si algo falla, quien llama hace rollback y no queda nada."""
    if forma_pago not in FORMAS_DE_PAGO:
        raise MayoreoInvalido("La forma de pago es efectivo o transferencia.")
    if not renglones:
        raise MayoreoInvalido("Agrega al menos un artículo.")
    if len(renglones) > RENGLONES_MAXIMOS:
        raise MayoreoInvalido(f"A lo más {RENGLONES_MAXIMOS} renglones por venta.")

    cliente = (
        await sesion.execute(
            text("SELECT nombre_comercial, mayoreo, estatus FROM clientes WHERE id = :c"),
            {"c": cliente_id},
        )
    ).mappings().first()
    if cliente is None or cliente["estatus"] == "baja":
        raise MayoreoInvalido("Ese cliente ya no existe.")
    if not cliente["mayoreo"]:
        raise MayoreoInvalido(
            f"«{cliente['nombre_comercial']}» no es cliente de mayoreo: es de una ruta."
        )
    bodega = (
        await sesion.execute(
            text("SELECT nombre FROM almacenes WHERE id = :a AND tipo = 'bodega' AND activo"),
            {"a": almacen_id},
        )
    ).scalar_one_or_none()
    if bodega is None:
        raise MayoreoInvalido("La venta de mayoreo sale de una bodega activa.")

    # Lo mismo de un producto en dos renglones se suma para revisar la existencia.
    partidas = []
    piezas_por_producto: dict[uuid.UUID, Decimal] = {}
    for r in renglones:
        if r.bultos <= 0:
            raise MayoreoInvalido("Hay un renglón sin cantidad.")
        if r.precio <= 0:
            raise MayoreoInvalido("Hay un renglón sin precio: el precio lo decides tú.")
        unidad = (
            await sesion.execute(
                text(
                    """
                    SELECT p.nombre, p.sku, u.factor,
                           (SELECT pr.precio FROM precios pr
                             WHERE pr.producto_id = p.id AND pr.unidad_codigo = u.unidad_codigo
                               AND pr.lista_id = (
                                     SELECT id FROM listas_precios WHERE activo
                                      ORDER BY es_default DESC, vigente_desde LIMIT 1)
                           ) AS precio_lista
                      FROM productos p
                      JOIN producto_unidades u ON u.producto_id = p.id
                     WHERE p.id = :p AND u.unidad_codigo = :u
                    """
                ),
                {"p": r.producto_id, "u": r.unidad_codigo},
            )
        ).mappings().first()
        if unidad is None:
            raise MayoreoInvalido(f"Ese artículo no se vende en {r.unidad_codigo}.")
        if r.bultos != r.bultos.to_integral_value():
            raise MayoreoInvalido(
                f"{unidad['nombre']}: la cantidad va en {r.unidad_codigo} enteras."
            )
        factor = Decimal(unidad["factor"])
        piezas = r.bultos * factor
        piezas_por_producto[r.producto_id] = (
            piezas_por_producto.get(r.producto_id, Decimal(0)) + piezas
        )
        partidas.append((r, unidad, factor, piezas))

    for producto_id, piezas in piezas_por_producto.items():
        hay = (
            await sesion.execute(
                text(
                    "SELECT COALESCE((SELECT cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p FOR UPDATE), 0)"
                ),
                {"a": almacen_id, "p": producto_id},
            )
        ).scalar_one()
        if Decimal(hay) < piezas:
            nombre = next(u["nombre"] for (r, u, _, _) in partidas if r.producto_id == producto_id)
            raise MayoreoInvalido(
                f"{nombre}: la bodega tiene {Decimal(hay).normalize():f} piezas y la venta "
                f"pide {piezas.normalize():f}."
            )

    venta_id = uuid.uuid4()
    consecutivo = (await sesion.execute(text("SELECT nextval('seq_folio_mayoreo')"))).scalar_one()
    folio = f"MY-{consecutivo:06d}"
    total = sum((_dos(r.bultos * r.precio) for (r, _, _, _) in partidas), Decimal(0))
    ahora = datetime.now(UTC)
    await sesion.execute(
        text(
            """
            INSERT INTO ventas_mayoreo (id, folio, cliente_id, almacen_id, fecha, forma_pago,
                                        referencia_pago, total, nota, vendida_por, creada_en)
            VALUES (:id, :folio, :c, :a, :fecha, :forma, :ref, :total, :nota, :quien, :ahora)
            """
        ),
        {
            "id": venta_id, "folio": folio, "c": cliente_id, "a": almacen_id,
            "fecha": fecha or date.today(), "forma": forma_pago,
            "ref": (referencia_pago or "").strip()[:80] or None, "total": total,
            "nota": (nota or "").strip()[:600] or None, "quien": quien, "ahora": ahora,
        },
    )
    for r, unidad, factor, piezas in partidas:
        await sesion.execute(
            text(
                """
                INSERT INTO venta_mayoreo_partidas
                    (venta_id, producto_id, unidad_codigo, factor, bultos, cantidad, precio,
                     precio_lista, importe)
                VALUES (:v, :p, :u, :factor, :bultos, :piezas, :precio, :lista, :importe)
                """
            ),
            {
                "v": venta_id, "p": r.producto_id, "u": r.unidad_codigo, "factor": factor,
                "bultos": r.bultos, "piezas": piezas, "precio": r.precio,
                "lista": unidad["precio_lista"], "importe": _dos(r.bultos * r.precio),
            },
        )
    for producto_id, piezas in piezas_por_producto.items():
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   documento_tipo, documento_id, usuario_id, fecha_servidor)
                VALUES ('venta', :a, NULL, :p, :cant, 'venta_mayoreo', :doc, :quien, :ahora)
                """
            ),
            {"a": almacen_id, "p": producto_id, "cant": piezas, "doc": venta_id,
             "quien": quien, "ahora": ahora},
        )
        await sesion.execute(
            text(
                "UPDATE existencias SET cantidad = cantidad - :cant, actualizado_en = :ahora "
                " WHERE almacen_id = :a AND producto_id = :p"
            ),
            {"a": almacen_id, "p": producto_id, "cant": piezas, "ahora": ahora},
        )
    return venta_id, folio


async def cancelar(sesion, venta_id: uuid.UUID, *, motivo: str, quien: uuid.UUID | None) -> str:
    """Cancela la venta y devuelve la mercancía a su bodega. No hace commit."""
    limpio = " ".join((motivo or "").split())
    if len(limpio) < 3:
        raise MayoreoInvalido("Escribe por qué se cancela.")
    venta = (
        await sesion.execute(
            text("SELECT folio, estado, almacen_id FROM ventas_mayoreo WHERE id = :v FOR UPDATE"),
            {"v": venta_id},
        )
    ).mappings().first()
    if venta is None:
        raise MayoreoInvalido("Esa venta no existe.")
    if venta["estado"] == "cancelada":
        raise MayoreoInvalido(f"La venta {venta['folio']} ya estaba cancelada.")
    ahora = datetime.now(UTC)
    await sesion.execute(
        text(
            "UPDATE ventas_mayoreo SET estado = 'cancelada', cancelada_en = :ahora, "
            "       cancelada_por = :quien, motivo_cancelacion = :motivo WHERE id = :v"
        ),
        {"v": venta_id, "ahora": ahora, "quien": quien, "motivo": limpio[:300]},
    )
    piezas = (
        await sesion.execute(
            text(
                "SELECT producto_id, sum(cantidad) AS cantidad FROM venta_mayoreo_partidas "
                " WHERE venta_id = :v GROUP BY producto_id"
            ),
            {"v": venta_id},
        )
    ).mappings().all()
    for p in piezas:
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   documento_tipo, documento_id, usuario_id, fecha_servidor)
                VALUES ('devolucion', NULL, :a, :p, :cant, 'venta_mayoreo', :doc, :quien, :ahora)
                """
            ),
            {"a": venta["almacen_id"], "p": p["producto_id"], "cant": p["cantidad"],
             "doc": venta_id, "quien": quien, "ahora": ahora},
        )
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:a, :p, :cant, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :cant, actualizado_en = :ahora
                """
            ),
            {"a": venta["almacen_id"], "p": p["producto_id"], "cant": p["cantidad"],
             "ahora": ahora},
        )
    return venta["folio"]


# ---------------------------------------------------------------------------
# Leer
# ---------------------------------------------------------------------------


async def ventas_del_periodo(sesion, desde: date, hasta: date) -> list[dict]:
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    """
                    SELECT v.id, v.folio, v.fecha, v.estado, v.total, v.forma_pago,
                           c.nombre_comercial AS cliente, a.nombre AS bodega,
                           u.nombre AS vendida_por,
                           (SELECT count(*) FROM venta_mayoreo_partidas
                             WHERE venta_id = v.id) AS renglones,
                           (SELECT sum(cantidad) FROM venta_mayoreo_partidas
                             WHERE venta_id = v.id) AS piezas
                      FROM ventas_mayoreo v
                      JOIN clientes c ON c.id = v.cliente_id
                      JOIN almacenes a ON a.id = v.almacen_id
                      LEFT JOIN usuarios u ON u.id = v.vendida_por
                     WHERE v.fecha BETWEEN :desde AND :hasta
                     ORDER BY v.fecha DESC, v.folio DESC
                     LIMIT 500
                    """
                ),
                {"desde": desde, "hasta": hasta},
            )
        ).mappings().all()
    ]


async def detalle(sesion, venta_id: uuid.UUID) -> dict | None:
    venta = (
        await sesion.execute(
            text(
                """
                SELECT v.*, c.nombre_comercial AS cliente, c.codigo AS cliente_codigo,
                       a.nombre AS bodega, u.nombre AS vendida_por_nombre,
                       x.nombre AS cancelada_por_nombre
                  FROM ventas_mayoreo v
                  JOIN clientes c ON c.id = v.cliente_id
                  JOIN almacenes a ON a.id = v.almacen_id
                  LEFT JOIN usuarios u ON u.id = v.vendida_por
                  LEFT JOIN usuarios x ON x.id = v.cancelada_por
                 WHERE v.id = :v
                """
            ),
            {"v": venta_id},
        )
    ).mappings().first()
    if venta is None:
        return None
    partidas = (
        await sesion.execute(
            text(
                """
                SELECT d.*, p.sku, p.nombre
                  FROM venta_mayoreo_partidas d
                  JOIN productos p ON p.id = d.producto_id
                 WHERE d.venta_id = :v
                 ORDER BY d.id
                """
            ),
            {"v": venta_id},
        )
    ).mappings().all()
    return {**dict(venta), "partidas": [dict(p) for p in partidas]}


def ticket(venta: dict) -> str:
    """El ticket para compartir por WhatsApp, como los de la app."""
    lineas = [
        "*VENTA DE MAYOREO*",
        f"Folio {venta['folio']} · {venta['fecha']:%d/%m/%Y}",
        f"Cliente: {venta['cliente']}",
        "",
    ]
    for p in venta["partidas"]:
        bultos = Decimal(p["bultos"]).normalize()
        lineas.append(
            f"• {p['nombre']}: {bultos:f} {p['unidad_codigo']} × ${Decimal(p['precio']):,.2f}"
            f" = ${Decimal(p['importe']):,.2f}"
        )
    lineas += ["", f"*Total: ${Decimal(venta['total']):,.2f}* ({venta['forma_pago']})"]
    if venta["estado"] == "cancelada":
        lineas.append(f"CANCELADA: {venta['motivo_cancelacion']}")
    return "\n".join(lineas)
