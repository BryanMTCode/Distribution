"""Mover mercancía de la bodega al camión, de regreso, y de un camión a otro, desde
el panel (ADR 0002 §135, §187).

«Quiero en el dashboard poder mover mercancía a los camiones y viceversa.» Y
después: «también quiero que me permita mover mercancía entre camiones de ellos,
solo en el dashboard».

────────────────────────────────────────────────────────────────────────────
POR QUÉ NO ES UN TRASPASO NI UNA CARGA
────────────────────────────────────────────────────────────────────────────
El camión tiene dueño (§0.2): su teléfono vende sin señal con su propio
inventario. El traspaso entre bodegas (`traspasos.py`) no toca camiones por eso:
le cambiaría el inventario bajo los pies a alguien que está vendiendo. La carga
sí sube al camión, pero es una al día, para el corte de mañana.

Lo que el teléfono sí sabe recibir de la oficina es una **diferencia** —«súmale
5», «quítale 3»—: el ajuste del camión (0032, 0037). La suma a lo que tenga, haya
vendido o no en medio, y por eso no pisa nada. Mover es entonces **dos ajustes
atados**, de tipo `traspaso`: uno que baja en el origen y otro que sube en el
destino, cada uno con la nota que nombra al otro. El del camión viaja a su
teléfono; el de la bodega no viaja a ninguno.

────────────────────────────────────────────────────────────────────────────
LO QUE NO SE DEJA
────────────────────────────────────────────────────────────────────────────
Sacar del origen más de lo que el sistema dice que hay. En la bodega es la regla
de siempre. En el camión, el sistema puede ir atrás de su teléfono —ventas que no
han subido—, así que lo que dice es lo más que puede traer, nunca menos: si el
teléfono vendió algo que no ha subido, regresar lo que el sistema dice regresaría
mercancía que ya no está. Por eso lo que se puede bajar es lo que el sistema dice,
y el corte de ese día lo deja ver si algo no cuadró.

Todo va en una transacción: o se mueve todo lo capturado, o nada. Y es UNA
operación (§146), por muchos artículos que lleve: todos sus ajustes, las dos
mitades de cada artículo, van juntos con un solo folio, y en Operaciones (§119)
anularla deshace todo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from app.infra.ajuste_de_existencia import (
    UnaSolaOperacion,
    existencia_con_candado,
    registrar_ajuste,
)


class MovimientoRechazado(Exception):
    """No se puede así; el mensaje dice por qué. No se movió nada."""


@dataclass(frozen=True)
class Movimiento:
    # El de la operación, y el de cada mitad (el primero es el mismo).
    folio: str
    folios: list[str]
    articulos: int
    piezas: Decimal
    mensaje: str


async def almacen(sesion, almacen_id: uuid.UUID, tipo: str) -> dict:
    """El almacén, si es del tipo que se pide y está activo."""
    fila = (
        await sesion.execute(
            text("SELECT a.id, a.codigo, a.nombre, a.tipo, a.responsable_id, "
                 "       u.nombre AS responsable "
                 "  FROM almacenes a LEFT JOIN usuarios u ON u.id = a.responsable_id "
                 " WHERE a.id = :a AND a.activo"),
            {"a": almacen_id},
        )
    ).mappings().first()
    if fila is None or fila["tipo"] != tipo:
        raise MovimientoRechazado(
            "Elige un camión." if tipo == "camion" else "Elige una bodega."
        )
    return dict(fila)


async def lo_que_hay(sesion, almacen_id: uuid.UUID) -> list[dict]:
    """Lo que tiene el almacén, por familia, con sus presentaciones de la más
    grande a la más chica."""
    filas = await sesion.execute(
        text(
            """
            SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_base, e.cantidad,
                   c.id AS familia_id, c.nombre AS familia, pu.presentaciones
              FROM existencias e
              JOIN productos p ON p.id = e.producto_id
              LEFT JOIN categorias c ON c.id = p.categoria_id
              LEFT JOIN LATERAL (
                    SELECT json_agg(json_build_object(
                             'unidad', u.unidad_codigo, 'factor', u.factor::text)
                           ORDER BY u.factor DESC) AS presentaciones
                      FROM producto_unidades u
                     WHERE u.producto_id = p.id AND u.activo
              ) pu ON true
             WHERE e.almacen_id = :a AND e.cantidad > 0
             -- Por familia, en el orden de la hoja de la dirección (§89).
             ORDER BY (c.id IS NULL), c.orden, c.nombre, p.nombre
            """
        ),
        {"a": almacen_id},
    )
    return [dict(f) for f in filas.mappings()]


async def mover(
    sesion,
    *,
    camion_id: uuid.UUID,
    bodega_id: uuid.UUID,
    al_camion: bool,
    cantidades: dict[uuid.UUID, Decimal],
    nota: str,
    quien: uuid.UUID,
) -> Movimiento:
    """Mueve `cantidades` (en unidad base) entre la bodega y el camión. No hace
    commit: quien llama decide la transacción."""
    camion = await almacen(sesion, camion_id, "camion")
    bodega = await almacen(sesion, bodega_id, "bodega")
    origen, destino = (bodega, camion) if al_camion else (camion, bodega)
    return await _mover(sesion, origen=origen, destino=destino, cantidades=cantidades,
                        nota=nota, quien=quien)


async def mover_entre_camiones(
    sesion,
    *,
    origen_id: uuid.UUID,
    destino_id: uuid.UUID,
    cantidades: dict[uuid.UUID, Decimal],
    nota: str,
    quien: uuid.UUID,
) -> Movimiento:
    """De un camión a otro (§187), igual que con la bodega: dos ajustes atados,
    y cada mitad le llega a su teléfono. No hace commit."""
    if origen_id == destino_id:
        raise MovimientoRechazado("Elige dos camiones distintos.")
    origen = await almacen(sesion, origen_id, "camion")
    destino = await almacen(sesion, destino_id, "camion")
    return await _mover(sesion, origen=origen, destino=destino, cantidades=cantidades,
                        nota=nota, quien=quien)


async def _mover(
    sesion,
    *,
    origen: dict,
    destino: dict,
    cantidades: dict[uuid.UUID, Decimal],
    nota: str,
    quien: uuid.UUID,
) -> Movimiento:
    camiones = [a for a in (origen, destino) if a["tipo"] == "camion"]
    for camion in camiones:
        if camion["responsable_id"] is None:
            raise MovimientoRechazado(
                f"{camion['nombre']} no tiene vendedor: no hay teléfono al que llegue."
            )
    pedidas = {p: c for p, c in cantidades.items() if c != 0}
    if not pedidas:
        raise MovimientoRechazado("Escribe cuánto mueves de al menos un artículo.")
    if any(c < 0 for c in pedidas.values()):
        raise MovimientoRechazado("Las cantidades van en positivo.")

    nombres = {
        f.id: f.nombre
        for f in await sesion.execute(
            text("SELECT id, nombre FROM productos WHERE id = ANY(:ids)"),
            {"ids": list(pedidas)},
        )
    }
    if len(nombres) != len(pedidas):
        raise MovimientoRechazado("Uno de los artículos ya no existe.")

    # Primero se revisa todo, con candado; después se escribe. Así no queda
    # movida la mitad si el quinto renglón no alcanza.
    actuales: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    no_alcanza = []
    for producto_id, cantidad in sorted(pedidas.items(), key=lambda x: str(x[0])):
        en_origen = await existencia_con_candado(sesion, origen["id"], producto_id)
        en_destino = await existencia_con_candado(sesion, destino["id"], producto_id)
        if cantidad > en_origen:
            no_alcanza.append(
                f"{nombres[producto_id]} (hay {_piezas(en_origen)}, mueves {_piezas(cantidad)})"
            )
        actuales[producto_id] = (en_origen, en_destino)
    if no_alcanza:
        raise MovimientoRechazado(
            f"No alcanza en {origen['nombre']}: {'; '.join(no_alcanza)}. No se movió nada."
        )

    adicional = " ".join((nota or "").split())
    folios = []
    # La cabeza es la primera mitad que sale; lo demás va con ella.
    operacion = UnaSolaOperacion()
    for producto_id, cantidad in pedidas.items():
        en_origen, en_destino = actuales[producto_id]
        lado = f"De {origen['nombre']} a {destino['nombre']}"
        folio_sale = await registrar_ajuste(
            sesion,
            almacen_id=origen["id"], producto_id=producto_id,
            es_camion=origen["tipo"] == "camion", tipo="traspaso",
            actual=en_origen, delta=-cantidad, contado=None,
            nota=f"{lado}{': ' + adicional if adicional else ''}", quien=quien,
            operacion=operacion,
        )
        folio_entra = await registrar_ajuste(
            sesion,
            almacen_id=destino["id"], producto_id=producto_id,
            es_camion=destino["tipo"] == "camion", tipo="traspaso",
            actual=en_destino, delta=cantidad, contado=None,
            nota=f"{lado} (sale con {folio_sale})"
                 f"{': ' + adicional if adicional else ''}",
            quien=quien, operacion=operacion,
        )
        folios += [folio_sale, folio_entra]

    piezas = sum(pedidas.values(), Decimal(0))
    a_quien = (f"Al teléfono de {camiones[0]['responsable']} le llega"
               if len(camiones) == 1 else
               f"A los teléfonos de {camiones[0]['responsable']} y "
               f"{camiones[1]['responsable']} les llega")
    return Movimiento(
        folio=operacion.folio,
        folios=folios,
        articulos=len(pedidas),
        piezas=piezas,
        mensaje=(
            f"Se movieron {len(pedidas)} artículo(s), {_piezas(piezas)} piezas, de "
            f"{origen['nombre']} a {destino['nombre']}, en una sola operación "
            f"({operacion.folio}). {a_quien} al sincronizar, y se suma a lo que traiga."
        ),
    )


def _piezas(cantidad: Decimal) -> str:
    return f"{cantidad.normalize():f}"
