"""Traspaso de mercancía entre bodegas.

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «quiero traspasar mercancía entre almacenes
desde la app». Hasta hoy la única forma de mover mercancía de una bodega a otra
eran dos documentos sueltos —una salida aquí, una entrada allá— y ningún papel
que atara los dos lados: «¿quién movió esas 18 cajas, de dónde a dónde?» no
tenía qué leer.

────────────────────────────────────────────────────────────────────────────
SOLO ENTRE BODEGAS, Y POR QUÉ
────────────────────────────────────────────────────────────────────────────
El camión tiene dueño exclusivo (§0.2): la oficina no le escribe existencias.
Lo que sube al camión entra con una CARGA y lo que baja, con la DEVOLUCIÓN que el
vendedor declara en su teléfono. Un traspaso que tocara un camión le cambiaría el
inventario bajo los pies a alguien que está vendiendo sin señal.

────────────────────────────────────────────────────────────────────────────
UN SOLO PASO, Y NO SE QUEDA EN TRÁNSITO
────────────────────────────────────────────────────────────────────────────
La devolución del camión pasa por tránsito porque la declara una persona y la
recibe otra (la bodega cuenta lo que de verdad llegó). Entre bodegas, quien
traspasa es la oficina y lo que mueve es su propio inventario: el documento nace
`aceptado`, con los dos lados en la misma transacción.

Y nace aceptado también por otra razón: el disparador de `traspasos` publica al
teléfono del vendedor cuando el ESTADO cambia (migración 0036). Un traspaso de la
oficina no le importa a ningún teléfono, y como nunca cambia de estado, nunca se
publica.

────────────────────────────────────────────────────────────────────────────
NO DEJA LA BODEGA DE ORIGEN EN NEGATIVO
────────────────────────────────────────────────────────────────────────────
Es la regla de las salidas de bodega: lo que se está tecleando ahora, con el
anaquel a la vista, no es un hecho que llega tarde (§0.1) sino una captura, y no
se puede mover lo que el sistema dice que no hay. Las existencias del origen se
bloquean (`FOR UPDATE`) antes de comparar: dos traspasos al mismo tiempo no
pueden sacar dos veces la misma caja.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text

from app.api.admin.comun import CapturaInvalida, sin_decimales, texto_o_nulo
from app.api.admin.entradas import _leer_bultos
from app.domain.importes import CantidadInvalida, cantidad_base

TIPOS_TRASPASABLES = ("bodega",)


class TraspasoRechazado(Exception):
    """No se puede así; el mensaje dice por qué y qué hacer."""


@dataclass(frozen=True)
class PedidoDeTraspaso:
    producto_id: uuid.UUID
    unidad: str
    # Texto, como se tecleó: se valida con las reglas de siempre (bultos enteros).
    cantidad: str


@dataclass(frozen=True)
class TraspasoHecho:
    id: uuid.UUID
    folio: str
    mensaje: str


async def traspasar_entre_bodegas(
    sesion,
    *,
    origen_id: uuid.UUID,
    destino_id: uuid.UUID,
    pedidos: list[PedidoDeTraspaso],
    nota: str = "",
    quien: uuid.UUID,
) -> TraspasoHecho:
    """Mueve la mercancía de una bodega a otra. Todo o nada."""
    if origen_id == destino_id:
        raise TraspasoRechazado("El origen y el destino son la misma bodega.")
    if not pedidos:
        raise TraspasoRechazado("Falta qué traspasar: escribe cuántos de al menos un producto.")

    almacenes = {
        a["id"]: a
        for a in (
            await sesion.execute(
                text("SELECT id, nombre, tipo FROM almacenes WHERE id = ANY(:ids) AND activo"),
                {"ids": [origen_id, destino_id]},
            )
        ).mappings()
    }
    for cual, aid in (("origen", origen_id), ("destino", destino_id)):
        a = almacenes.get(aid)
        if a is None:
            raise TraspasoRechazado(f"El almacén de {cual} no existe o está inactivo.")
        if a["tipo"] not in TIPOS_TRASPASABLES:
            raise TraspasoRechazado(
                f"{a['nombre']} es un {a['tipo']}, no una bodega. A un camión se le sube "
                "con una carga y baja con la devolución del vendedor: la oficina no le "
                "mueve el inventario por su cuenta."
            )

    # Cada pedido, a unidad base, sumado por producto: dos renglones del mismo
    # producto en distintas presentaciones son un solo movimiento.
    por_producto: dict[uuid.UUID, Decimal] = {}
    nombres: dict[uuid.UUID, str] = {}
    for pedido in pedidos:
        fila = (
            await sesion.execute(
                text(
                    "SELECT p.nombre, u.factor FROM productos p "
                    "  LEFT JOIN producto_unidades u ON u.producto_id = p.id "
                    "       AND u.unidad_codigo = :u AND u.activo "
                    " WHERE p.id = :p AND p.activo"
                ),
                {"p": pedido.producto_id, "u": pedido.unidad},
            )
        ).mappings().first()
        if fila is None:
            raise TraspasoRechazado("Uno de los productos ya no está activo.")
        if fila["factor"] is None:
            raise TraspasoRechazado(f"{fila['nombre']} no tiene la presentación {pedido.unidad}.")
        try:
            en_base = cantidad_base(_leer_bultos(pedido.cantidad), Decimal(fila["factor"]))
        except (CapturaInvalida, CantidadInvalida) as e:
            raise TraspasoRechazado(f"{fila['nombre']}: {e}") from e
        anterior = por_producto.get(pedido.producto_id, Decimal(0))
        por_producto[pedido.producto_id] = anterior + en_base
        nombres[pedido.producto_id] = fila["nombre"]

    # Lo que hay en el origen, bloqueado hasta el commit.
    hay = {
        f["producto_id"]: Decimal(f["cantidad"])
        for f in (
            await sesion.execute(
                text(
                    "SELECT producto_id, cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = ANY(:ps) "
                    "   FOR UPDATE"
                ),
                {"a": origen_id, "ps": list(por_producto)},
            )
        ).mappings()
    }
    faltan = [
        f"{nombres[p]} (hay {sin_decimales(hay.get(p, Decimal(0)))}, "
        f"pides {sin_decimales(cantidad)})"
        for p, cantidad in por_producto.items()
        if hay.get(p, Decimal(0)) < cantidad
    ]
    if faltan:
        raise TraspasoRechazado(
            f"No alcanza en {almacenes[origen_id]['nombre']}: " + "; ".join(faltan)
            + ". Si la mercancía sí está en el anaquel, primero captura la entrada "
            "por conteo para que el sistema la tenga."
        )

    ahora = datetime.now(UTC)
    traspaso_id = uuid.uuid4()
    consecutivo = (await sesion.execute(text("SELECT nextval('seq_folio_traspaso')"))).scalar_one()
    folio = f"TR-{consecutivo:06d}"
    await sesion.execute(
        text(
            """
            INSERT INTO traspasos (id, folio, almacen_origen_id, almacen_destino_id,
                                   estado, solicitado_por, resuelto_por, resuelto_en,
                                   fecha_operativa, observaciones)
            VALUES (:id, :folio, :o, :d, 'aceptado', :quien, :quien, :ahora,
                    :hoy, :nota)
            """
        ),
        {
            "id": traspaso_id,
            "folio": folio,
            "o": origen_id,
            "d": destino_id,
            "quien": quien,
            "ahora": ahora,
            "hoy": date.today(),
            "nota": texto_o_nulo(nota, maximo=500),
        },
    )
    for producto, cantidad in por_producto.items():
        await sesion.execute(
            text(
                "INSERT INTO traspaso_detalle (id, traspaso_id, producto_id, cantidad, "
                "                              cantidad_recibida) "
                "VALUES (:id, :t, :p, :c, :c)"
            ),
            {"id": uuid.uuid4(), "t": traspaso_id, "p": producto, "c": cantidad},
        )
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   documento_tipo, documento_id, usuario_id, fecha_servidor)
                VALUES ('traspaso', :o, :d, :p, :c, 'traspaso', :doc, :quien, :ahora)
                """
            ),
            {
                "o": origen_id, "d": destino_id, "p": producto, "c": cantidad,
                "doc": traspaso_id, "quien": quien, "ahora": ahora,
            },
        )
        # La caché, en la misma transacción que el asiento: los dos lados o ninguno.
        await sesion.execute(
            text(
                "UPDATE existencias SET cantidad = cantidad - :c, actualizado_en = :ahora "
                " WHERE almacen_id = :o AND producto_id = :p"
            ),
            {"c": cantidad, "o": origen_id, "p": producto, "ahora": ahora},
        )
        await sesion.execute(
            text(
                """
                INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                VALUES (:d, :p, :c, :ahora)
                ON CONFLICT (almacen_id, producto_id) DO UPDATE
                   SET cantidad = existencias.cantidad + :c, actualizado_en = :ahora
                """
            ),
            {"c": cantidad, "d": destino_id, "p": producto, "ahora": ahora},
        )
    await sesion.commit()

    piezas = sum(por_producto.values(), Decimal(0))
    return TraspasoHecho(
        id=traspaso_id,
        folio=folio,
        mensaje=(
            f"{folio}: {len(por_producto)} producto(s), {sin_decimales(piezas)} piezas de "
            f"{almacenes[origen_id]['nombre']} a {almacenes[destino_id]['nombre']}."
        ),
    )


async def traspasos_entre_bodegas(sesion, limite: int = 50) -> list:
    """Los últimos traspasos de la oficina (de bodega a bodega)."""
    return list(
        (
            await sesion.execute(
                text(
                    """
                    SELECT t.id, t.folio, t.creado_en, t.observaciones,
                           o.nombre AS origen, d.nombre AS destino,
                           u.nombre AS quien,
                           COALESCE(x.renglones, 0) AS renglones,
                           COALESCE(x.piezas, 0) AS piezas
                      FROM traspasos t
                      JOIN almacenes o ON o.id = t.almacen_origen_id AND o.tipo = 'bodega'
                      JOIN almacenes d ON d.id = t.almacen_destino_id AND d.tipo = 'bodega'
                      LEFT JOIN usuarios u ON u.id = t.solicitado_por
                      LEFT JOIN LATERAL (
                            SELECT count(*) AS renglones, sum(cantidad) AS piezas
                              FROM traspaso_detalle WHERE traspaso_id = t.id
                      ) x ON true
                     ORDER BY t.creado_en DESC
                     LIMIT :limite
                    """
                ),
                {"limite": limite},
            )
        ).mappings().all()
    )
