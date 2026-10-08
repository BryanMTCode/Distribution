"""El cierre del vendedor: lo que la oficina ve y lo que pasa al resolverlo.

────────────────────────────────────────────────────────────────────────────
EL FLUJO (ADR 0002 §82)
────────────────────────────────────────────────────────────────────────────
1. El vendedor hace su corte en el teléfono (`corte.crear`): el efectivo que
   entrega. **No cuenta el camión**: lo que le queda lo calcula el sistema —lo
   que traía, más lo que se le cargó, menos lo que vendió—. Sale su ticket.
2. Pide la carga del día siguiente (`solicitud_carga.crear`).
3. El gerente resuelve las dos cosas POR SEPARADO, cada una en su lugar:
   · **cierra el corte** (`cerrar_corte_del_vendedor`) con las funciones del
     corte de siempre (`abrir_corte`, `guardar_arqueo`, `cerrar_corte` con
     `conteo_automatico`): sin diferencias de mercancía, así que lo único que
     puede ir a la cuenta del vendedor es el efectivo que no entregó;
   · **acepta la carga** (`aceptar_solicitud`) con las de la carga de siempre
     (`_bloqueos_para_cargar`, `mover_y_confirmar`): sale de la bodega principal
     y le llega al teléfono en su siguiente sincronización. Primero el corte: con
     el de hoy abierto, la carga de mañana no sale.

Solo hay UNA carga al día, y es para el día siguiente.

────────────────────────────────────────────────────────────────────────────
POR QUÉ VIVE AQUÍ Y NO EN UNA PANTALLA
────────────────────────────────────────────────────────────────────────────
Lo usan el panel (`/panel/cierres`) y la app del gerente (`/v1/cierres`). Si
cada uno tuviera su copia, el día que se arregle una la otra seguiría cerrando
cortes con la regla vieja.

────────────────────────────────────────────────────────────────────────────
SE PUEDE REINTENTAR
────────────────────────────────────────────────────────────────────────────
Las funciones del corte confirman por partes, así que cerrar no es una sola
transacción. Está escrito para que reintentar sea seguro: un corte ya cerrado
no se vuelve a cerrar, y la carga en borrador que quedó de un intento fallido
se reutiliza. La solicitud se marca aceptada en la MISMA transacción que mueve
el inventario de la carga: o pasan las dos, o ninguna.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.api.admin.cargas import _bloqueos_para_cargar, _leer_bultos, mover_y_confirmar
from app.api.admin.comun import CapturaInvalida, dinero
from app.api.admin.liquidaciones import (
    CorteNoExiste,
    CorteRechazado,
    _con_inicial,
    _efectivo_esperado,
    _renglones_calculados,
    abrir_corte,
    cerrar_corte,
    guardar_arqueo,
)
from app.domain.importes import CantidadInvalida, cantidad_base


class CierreNoExiste(Exception):
    """El corte o la solicitud no existen (404)."""


class CierreRechazado(Exception):
    """No se puede resolver ahora, y el mensaje dice por qué (409)."""


async def bodega_principal(sesion) -> dict | None:
    """De dónde sale la carga y a dónde entran las compras.

    La que se llama `BODEGA_PRINCIPAL` si existe; si no, la bodega activa más
    antigua. Una distribuidora de este tamaño tiene una, y adivinar entre varias
    sería peor que decir cuál se usó: el ticket y la carga nombran la bodega.
    """
    fila = (
        await sesion.execute(
            text(
                "SELECT id, codigo, nombre FROM almacenes "
                " WHERE activo AND tipo = 'bodega' "
                " ORDER BY (codigo = 'BODEGA_PRINCIPAL') DESC, creado_en, codigo "
                " LIMIT 1"
            )
        )
    ).mappings().first()
    return dict(fila) if fila else None


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
async def lista_de_cortes(sesion, *, dias_recientes: int = 7) -> dict:
    """Los cortes por cerrar y los que se cerraron en los últimos días.

    Cada elemento es un «cierre» con solo el corte: la carga se ve aparte.
    """
    pendientes = (
        await sesion.execute(
            text("SELECT id FROM cortes_vendedor WHERE estado = 'pendiente' "
                 " ORDER BY fecha_operativa, recibido_en")
        )
    ).scalars().all()
    cerrados = (
        await sesion.execute(
            text("SELECT id FROM cortes_vendedor "
                 " WHERE estado = 'cerrado' AND resuelto_en >= :desde "
                 " ORDER BY resuelto_en DESC LIMIT 50"),
            {"desde": datetime.now(UTC) - timedelta(days=dias_recientes)},
        )
    ).scalars().all()
    return {
        "pendientes": [await _armar(sesion, corte_id=c, solicitud_id=None) for c in pendientes],
        "recientes": [await _armar(sesion, corte_id=c, solicitud_id=None) for c in cerrados],
    }


async def lista_de_solicitudes(sesion, *, dias_recientes: int = 7) -> dict:
    """Las cargas pedidas por aceptar y las resueltas en los últimos días.

    Cada elemento es un «cierre» con solo la solicitud, y con
    `corte_por_cerrar` si el vendedor todavía debe su corte: esa carga espera.
    """
    pendientes = (
        await sesion.execute(
            text("SELECT id FROM solicitudes_carga WHERE estado = 'pendiente' "
                 " ORDER BY fecha_operativa, recibido_en")
        )
    ).scalars().all()
    resueltas = (
        await sesion.execute(
            text(
                "SELECT id FROM solicitudes_carga "
                " WHERE estado IN ('aceptada', 'rechazada') AND resuelta_en >= :desde "
                " ORDER BY resuelta_en DESC LIMIT 50"
            ),
            {"desde": datetime.now(UTC) - timedelta(days=dias_recientes)},
        )
    ).scalars().all()
    return {
        "pendientes": [await _armar(sesion, corte_id=None, solicitud_id=s) for s in pendientes],
        "recientes": [await _armar(sesion, corte_id=None, solicitud_id=s) for s in resueltas],
    }


async def cierres(sesion, *, dias_recientes: int = 7) -> dict:
    """El corte y la carga de cada vendedor, juntos.

    Así los revisaba la app del gerente anterior a la versión +28; se queda para
    que la que todavía no se actualiza siga funcionando. Las pantallas de ahora
    usan `lista_de_cortes` y `lista_de_solicitudes`.
    """
    cortes_pendientes = (
        await sesion.execute(
            text("SELECT id, vendedor_id, fecha_operativa FROM cortes_vendedor "
                 " WHERE estado = 'pendiente' ORDER BY fecha_operativa, recibido_en")
        )
    ).mappings().all()
    solicitudes_pendientes = (
        await sesion.execute(
            text("SELECT id, corte_id, vendedor_id, fecha_operativa FROM solicitudes_carga "
                 " WHERE estado = 'pendiente' ORDER BY fecha_operativa, recibido_en")
        )
    ).mappings().all()

    # La solicitud va con el corte que la originó, o con el corte de ese mismo
    # vendedor del día anterior a la carga: si el vendedor rehizo su corte
    # después de pedirla, la solicitud apunta al corte reemplazado, y mostrarlos
    # como dos cierres separados haría que el gerente aceptara la mitad.
    def va_con(s, c) -> bool:
        return s["corte_id"] == c["id"] or (
            s["vendedor_id"] == c["vendedor_id"]
            and s["fecha_operativa"] == c["fecha_operativa"] + timedelta(days=1)
        )

    pendientes: list[dict] = []
    usadas: set[uuid.UUID] = set()
    for c in cortes_pendientes:
        solicitud = next(
            (s["id"] for s in solicitudes_pendientes
             if s["id"] not in usadas and va_con(s, c)),
            None,
        )
        if solicitud is not None:
            usadas.add(solicitud)
        pendientes.append(await _armar(sesion, corte_id=c["id"], solicitud_id=solicitud))
    for s in solicitudes_pendientes:
        if s["id"] in usadas:
            continue
        pendientes.append(
            await _armar(sesion, corte_id=await _corte_vigente(sesion, s["corte_id"]),
                         solicitud_id=s["id"])
        )

    desde = datetime.now(UTC) - timedelta(days=dias_recientes)
    resueltas = (
        await sesion.execute(
            text(
                "SELECT id, corte_id FROM solicitudes_carga "
                " WHERE estado IN ('aceptada', 'rechazada') AND resuelta_en >= :desde "
                " ORDER BY resuelta_en DESC LIMIT 50"
            ),
            {"desde": desde},
        )
    ).mappings().all()
    recientes = [
        await _armar(sesion, corte_id=s["corte_id"], solicitud_id=s["id"]) for s in resueltas
    ]
    return {"pendientes": pendientes, "recientes": recientes}


async def un_cierre(
    sesion,
    *,
    corte_id: uuid.UUID | None = None,
    solicitud_id: uuid.UUID | None = None,
    solo: bool = False,
) -> dict:
    """Un cierre. Con `solo`, la solicitud sin buscarle su corte."""
    if solicitud_id is not None and corte_id is None and not solo:
        corte_id = await _corte_vigente(
            sesion,
            (
                await sesion.execute(
                    text("SELECT corte_id FROM solicitudes_carga WHERE id = :s"),
                    {"s": solicitud_id},
                )
            ).scalar_one_or_none(),
        )
    return await _armar(sesion, corte_id=corte_id, solicitud_id=solicitud_id)


async def _corte_vigente(sesion, corte_id: uuid.UUID | None) -> uuid.UUID | None:
    """El corte, o el que lo reemplazó si el vendedor lo rehízo ese mismo día."""
    if corte_id is None:
        return None
    vigente = (
        await sesion.execute(
            text(
                """
                SELECT COALESCE(
                         (SELECT n.id FROM cortes_vendedor n
                           WHERE n.vendedor_id = k.vendedor_id
                             AND n.fecha_operativa = k.fecha_operativa
                             AND n.estado <> 'reemplazado'
                           ORDER BY n.recibido_en DESC LIMIT 1),
                         k.id)
                  FROM cortes_vendedor k WHERE k.id = :c
                """
            ),
            {"c": corte_id},
        )
    ).scalar_one_or_none()
    return vigente


async def _armar(
    sesion, *, corte_id: uuid.UUID | None, solicitud_id: uuid.UUID | None
) -> dict:
    corte = await _corte(sesion, corte_id) if corte_id else None
    solicitud = await _solicitud(sesion, solicitud_id) if solicitud_id else None
    if corte is None and solicitud is None:
        raise CierreNoExiste("Ese corte o esa solicitud no existen.")
    vendedor_id = (corte or solicitud)["vendedor_id"]
    vendedor = (
        await sesion.execute(
            text(
                "SELECT u.id, u.codigo, u.nombre, a.nombre AS camion "
                "  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id "
                " WHERE u.id = :v"
            ),
            {"v": vendedor_id},
        )
    ).mappings().one()
    return {
        "vendedor_id": vendedor["id"],
        "vendedor_codigo": vendedor["codigo"],
        "vendedor": vendedor["nombre"],
        "camion": vendedor["camion"],
        "corte": corte,
        "solicitud": solicitud,
        "corte_por_cerrar": (
            await _corte_por_cerrar(sesion, vendedor_id, solicitud["fecha_operativa"])
            if solicitud is not None and solicitud["estado"] == "pendiente"
            else None
        ),
    }


async def _corte_por_cerrar(sesion, vendedor_id: uuid.UUID, antes_de: date) -> dict | None:
    """El corte que el vendedor mandó y nadie ha cerrado: la carga espera por él."""
    fila = (
        await sesion.execute(
            text(
                "SELECT id, fecha_operativa FROM cortes_vendedor "
                " WHERE vendedor_id = :v AND estado = 'pendiente' AND fecha_operativa < :d "
                " ORDER BY fecha_operativa LIMIT 1"
            ),
            {"v": vendedor_id, "d": antes_de},
        )
    ).mappings().first()
    return dict(fila) if fila else None


async def _corte(sesion, corte_id: uuid.UUID) -> dict | None:
    c = (
        await sesion.execute(
            text(
                "SELECT k.*, l.folio AS liquidacion_folio, u.almacen_id AS camion_id "
                "  FROM cortes_vendedor k "
                "  JOIN usuarios u ON u.id = k.vendedor_id "
                "  LEFT JOIN liquidaciones l ON l.id = k.liquidacion_id "
                " WHERE k.id = :c"
            ),
            {"c": corte_id},
        )
    ).mappings().first()
    if c is None:
        return None
    esperado = await _efectivo_esperado(sesion, c["vendedor_id"], c["fecha_operativa"])
    return {
        "id": c["id"],
        "vendedor_id": c["vendedor_id"],
        "fecha_operativa": c["fecha_operativa"],
        "estado": c["estado"],
        "efectivo_declarado": c["efectivo_declarado"],
        "efectivo_esperado": esperado,
        "diferencia_efectivo": c["efectivo_declarado"] - esperado,
        "observaciones": c["observaciones"],
        "recibido_en": c["recibido_en"],
        "resuelto_en": c["resuelto_en"],
        "liquidacion_folio": c["liquidacion_folio"],
        "nota": c["nota"],
        "renglones": await _lo_que_queda(sesion, dict(c)),
    }


async def _lo_que_queda(sesion, corte: dict) -> list[dict]:
    """Producto por producto: lo que traía, lo cargado, lo vendido y lo que le queda.

    Nadie lo cuenta (ADR 0002 §82): ya cerrado, es lo que quedó escrito en su
    liquidación; por cerrar, es el cálculo que va a hacer el cierre —el mismo
    `_renglones_calculados`—, al momento: una venta que sincroniza tarde ya
    cuenta.
    """
    liquidacion_id = corte["liquidacion_id"]
    if liquidacion_id is None:
        carga = await _carga_del_corte(sesion, corte)
        if carga is not None and carga["estado"] == "liquidada":
            liquidacion_id = (
                await sesion.execute(
                    text("SELECT id FROM liquidaciones WHERE carga_id = :c"), {"c": carga["id"]}
                )
            ).scalar_one_or_none()
        elif carga is not None:
            return await _con_nombres(
                sesion,
                [
                    {**r, "traia": r["inicial"], "queda": r["en_camion"]}
                    for r in _con_inicial(await _renglones_calculados(sesion, carga["id"]))
                ],
            )
    if liquidacion_id is not None:
        filas = (
            await sesion.execute(
                text(
                    "SELECT producto_id, cant_inicial AS traia, cant_cargada AS cargada, "
                    "       cant_vendida AS vendida, cant_merma AS merma, "
                    "       cant_devuelta AS devuelta, cant_contada AS queda "
                    "  FROM liquidacion_detalle WHERE liquidacion_id = :l"
                ),
                {"l": liquidacion_id},
            )
        ).mappings().all()
        return await _con_nombres(sesion, [dict(f) for f in filas])
    # Sin carga que cortar: lo que el camión tiene.
    filas = (
        await sesion.execute(
            text(
                "SELECT e.producto_id, e.cantidad AS traia, e.cantidad AS queda "
                "  FROM existencias e JOIN usuarios u ON u.almacen_id = e.almacen_id "
                " WHERE u.id = :v AND e.cantidad <> 0"
            ),
            {"v": corte["vendedor_id"]},
        )
    ).mappings().all()
    return await _con_nombres(sesion, [dict(f) for f in filas])


async def _con_nombres(sesion, filas: list[dict]) -> list[dict]:
    if not filas:
        return []
    productos = {
        p["id"]: p
        for p in (
            await sesion.execute(
                text("SELECT id, sku, nombre, unidad_base FROM productos WHERE id = ANY(:ids)"),
                {"ids": [f["producto_id"] for f in filas]},
            )
        ).mappings()
    }
    renglones = []
    for f in filas:
        p = productos[f["producto_id"]]
        queda = Decimal(f["queda"])
        renglones.append(
            {
                "producto_id": f["producto_id"],
                "sku": p["sku"],
                "nombre": p["nombre"],
                "unidad_base": p["unidad_base"],
                "traia": Decimal(f["traia"]),
                "cargada": Decimal(f.get("cargada") or 0),
                "vendida": Decimal(f.get("vendida") or 0),
                "merma": Decimal(f.get("merma") or 0),
                "devuelta": Decimal(f.get("devuelta") or 0),
                "queda": queda,
                # La app del gerente anterior a la versión +28 esperaba un conteo
                # del vendedor: se le da lo calculado, que es lo que se cierra.
                "contada": queda,
                "contado": True,
                "sistema": queda,
                "diferencia": Decimal(0),
            }
        )
    return sorted(renglones, key=lambda r: r["nombre"])


async def _carga_del_corte(sesion, corte: dict) -> dict | None:
    """La carga que el corte cierra.

    La que dijo el teléfono, si es del camión y sigue viva; si no —el teléfono
    no supo qué carga traía, o la de su payload no sirve—, la última que el
    camión tiene sin cortar hasta ese día.
    """
    carga = None
    if corte["carga_id"] is not None:
        carga = (
            await sesion.execute(
                text("SELECT id, folio, estado FROM cargas WHERE id = :c"),
                {"c": corte["carga_id"]},
            )
        ).mappings().first()
    if carga is None or carga["estado"] not in ("confirmada", "en_ruta", "liquidada"):
        carga = (
            await sesion.execute(
                text(
                    "SELECT id, folio, estado FROM cargas "
                    " WHERE vendedor_id = :v AND estado IN ('confirmada', 'en_ruta') "
                    "   AND fecha_operativa <= :d "
                    " ORDER BY fecha_operativa DESC LIMIT 1"
                ),
                {"v": corte["vendedor_id"], "d": corte["fecha_operativa"]},
            )
        ).mappings().first()
    return dict(carga) if carga else None


async def _solicitud(sesion, solicitud_id: uuid.UUID) -> dict | None:
    s = (
        await sesion.execute(
            text(
                "SELECT s.*, c.folio AS carga_folio, q.nombre AS resuelta_por_nombre "
                "  FROM solicitudes_carga s "
                "  LEFT JOIN cargas c ON c.id = s.carga_id "
                "  LEFT JOIN usuarios q ON q.id = s.resuelta_por "
                " WHERE s.id = :s"
            ),
            {"s": solicitud_id},
        )
    ).mappings().first()
    if s is None:
        return None
    bodega = await bodega_principal(sesion)
    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.producto_id, p.sku, p.nombre, p.unidad_base, d.unidad_codigo,
                       COALESCE(u.factor, 1) AS factor, d.bultos, d.cantidad,
                       d.cantidad_aceptada, COALESCE(e.cantidad, 0) AS en_bodega
                  FROM solicitud_carga_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN producto_unidades u
                         ON u.producto_id = d.producto_id AND u.unidad_codigo = d.unidad_codigo
                  LEFT JOIN existencias e
                         ON e.producto_id = d.producto_id AND e.almacen_id = :bodega
                 WHERE d.solicitud_id = :s
                 ORDER BY p.nombre
                """
            ),
            {"s": solicitud_id, "bodega": bodega["id"] if bodega else None},
        )
    ).mappings().all()
    return {
        "id": s["id"],
        "vendedor_id": s["vendedor_id"],
        "corte_id": s["corte_id"],
        "fecha_operativa": s["fecha_operativa"],
        "estado": s["estado"],
        "observaciones": s["observaciones"],
        "recibido_en": s["recibido_en"],
        "resuelta_en": s["resuelta_en"],
        "resuelta_por": s["resuelta_por_nombre"],
        "motivo": s["motivo"],
        "carga_id": s["carga_id"],
        "carga_folio": s["carga_folio"],
        "bodega": bodega["nombre"] if bodega else None,
        "renglones": [dict(r) for r in renglones],
    }


# ---------------------------------------------------------------------------
# Cerrar el corte y aceptar la carga: cada uno por su lado
# ---------------------------------------------------------------------------
async def cerrar_corte_del_vendedor(sesion, corte_id: uuid.UUID, *, quien: uuid.UUID) -> str:
    """Cierra el corte que mandó el vendedor. Devuelve el aviso para la pantalla."""
    corte = (
        await sesion.execute(text("SELECT * FROM cortes_vendedor WHERE id = :c"), {"c": corte_id})
    ).mappings().first()
    if corte is None:
        raise CierreNoExiste("Ese corte no existe.")
    if corte["estado"] == "reemplazado":
        raise CierreRechazado(
            "Ese corte lo reemplazó otro más reciente del mismo vendedor: cierra ése."
        )
    if corte["estado"] != "pendiente":
        raise CierreRechazado(f"Ese corte ya está {corte['estado']}.")
    return await _cerrar_el_corte(sesion, dict(corte), quien)


async def aceptar_solicitud(
    sesion,
    solicitud_id: uuid.UUID,
    *,
    quien: uuid.UUID,
    bultos: dict[str, str] | None = None,
) -> str:
    """Crea y confirma la carga que pidió el vendedor.

    `bultos` corrige lo pedido renglón por renglón (de id de producto a cuántos
    bultos de la MISMA presentación que pidió); un «0» quita el renglón. Lo que
    no viene se acepta tal cual se pidió. Devuelve el aviso para la pantalla.
    """
    solicitud = (
        await sesion.execute(
            text("SELECT * FROM solicitudes_carga WHERE id = :s"), {"s": solicitud_id}
        )
    ).mappings().first()
    if solicitud is None:
        raise CierreNoExiste("Esa solicitud de carga no existe.")
    if solicitud["estado"] != "pendiente":
        raise CierreRechazado(f"Esa solicitud ya está {solicitud['estado']}.")
    debe = await _corte_por_cerrar(sesion, solicitud["vendedor_id"], solicitud["fecha_operativa"])
    if debe is not None:
        nombre = (
            await sesion.execute(
                text("SELECT nombre FROM usuarios WHERE id = :v"),
                {"v": solicitud["vendedor_id"]},
            )
        ).scalar_one()
        raise CierreRechazado(
            f"Primero cierra el corte de {nombre} del {debe['fecha_operativa']:%d/%m} "
            "(en «Corte del día»): con su día abierto, la carga de mañana no sale."
        )
    return await _crear_la_carga(sesion, dict(solicitud), quien, bultos or {})


async def aceptar(
    sesion,
    *,
    corte_id: uuid.UUID | None,
    solicitud_id: uuid.UUID | None,
    quien: uuid.UUID,
    bultos: dict[str, str] | None = None,
) -> str:
    """Cierra el corte (si lo hay) y acepta la carga pedida (si la hay), de un jalón.

    Así lo pedía la app del gerente anterior a la versión +28; se queda para que
    la que todavía no se actualiza siga funcionando.
    """
    if corte_id is None and solicitud_id is None:
        raise CierreRechazado("No hay nada que aceptar.")

    solicitud = None
    if solicitud_id is not None:
        solicitud = (
            await sesion.execute(
                text("SELECT * FROM solicitudes_carga WHERE id = :s"), {"s": solicitud_id}
            )
        ).mappings().first()
        if solicitud is None:
            raise CierreNoExiste("Esa solicitud de carga no existe.")
        if solicitud["estado"] != "pendiente":
            raise CierreRechazado(f"Esa solicitud ya está {solicitud['estado']}.")
        corte_id = corte_id or await _corte_vigente(sesion, solicitud["corte_id"])

    corte = None
    if corte_id is not None:
        corte = (
            await sesion.execute(
                text("SELECT * FROM cortes_vendedor WHERE id = :c"), {"c": corte_id}
            )
        ).mappings().first()
        if corte is None:
            raise CierreNoExiste("Ese corte no existe.")
        if solicitud is not None and corte["vendedor_id"] != solicitud["vendedor_id"]:
            raise CierreRechazado("El corte y la solicitud son de vendedores distintos.")
        if corte["estado"] == "reemplazado":
            raise CierreRechazado(
                "Ese corte lo reemplazó otro más reciente del mismo vendedor: acepta ése."
            )

    avisos: list[str] = []
    if corte is not None and corte["estado"] == "pendiente":
        avisos.append(await _cerrar_el_corte(sesion, dict(corte), quien))
    if solicitud is not None:
        avisos.append(await aceptar_solicitud(sesion, solicitud["id"], quien=quien, bultos=bultos))
    return " ".join(avisos)


async def _cerrar_el_corte(sesion, corte: dict, quien: uuid.UUID) -> str:
    """El corte del vendedor, cerrado con las funciones del corte de siempre."""
    carga = await _carga_del_corte(sesion, corte)
    if carga is None:
        await _marcar_corte(sesion, corte["id"], quien, None,
                            nota="no había carga abierta que liquidar")
        await sesion.commit()
        return (
            "El vendedor no tenía carga abierta: su corte queda como constancia, "
            "sin liquidación."
        )

    if carga["estado"] == "liquidada":
        # La oficina ya lo cortó a mano desde «Corte del día»: ese cierre manda.
        liquidacion = (
            await sesion.execute(
                text("SELECT id, folio FROM liquidaciones WHERE carga_id = :c"),
                {"c": carga["id"]},
            )
        ).mappings().first()
        await _marcar_corte(
            sesion, corte["id"], quien, liquidacion["id"] if liquidacion else None,
            nota="la oficina ya había cortado esa carga",
        )
        await sesion.commit()
        return (
            f"La carga {carga['folio']} ya estaba cortada"
            f"{' (' + liquidacion['folio'] + ')' if liquidacion else ''}: se queda ese corte."
        )

    try:
        liquidacion_id = await abrir_corte(sesion, carga["id"])
        # Nadie cuenta el camión: lo que le queda es lo que el sistema calcula
        # (`conteo_automatico`), y lo único que el vendedor declara es el efectivo.
        await guardar_arqueo(
            sesion,
            liquidacion_id,
            format(corte["efectivo_declarado"], "f"),
            corte["observaciones"] or "",
        )
        # La confirmación de «el teléfono terminó de subir» la da el corte mismo:
        # viaja en la cola DESPUÉS de todo lo que el vendedor hizo ese día.
        aviso = await cerrar_corte(
            sesion, liquidacion_id, quien=quien, confirmo_sincronizado=True,
            conteo_automatico=True,
        )
    except (CorteNoExiste, CorteRechazado, CapturaInvalida) as e:
        raise CierreRechazado(f"El corte no se pudo cerrar: {e}") from e

    await _marcar_corte(sesion, corte["id"], quien, liquidacion_id, nota=None)
    await sesion.commit()
    return aviso


async def _marcar_corte(
    sesion, corte_id: uuid.UUID, quien: uuid.UUID, liquidacion_id, *, nota: str | None
) -> None:
    await sesion.execute(
        text(
            "UPDATE cortes_vendedor "
            "   SET estado = 'cerrado', liquidacion_id = :l, resuelto_por = :q, "
            "       resuelto_en = now(), "
            "       nota = CASE WHEN CAST(:nota AS text) IS NULL THEN nota "
            "                   ELSE concat_ws('; ', nota, CAST(:nota AS text)) END "
            " WHERE id = :c"
        ),
        {"c": corte_id, "q": quien, "l": liquidacion_id, "nota": nota},
    )


async def _crear_la_carga(
    sesion, solicitud: dict, quien: uuid.UUID, bultos: dict[str, str]
) -> str:
    """La carga de mañana: sale de la bodega principal y se confirma ya."""
    dia: date = solicitud["fecha_operativa"]
    if dia < date.today():
        raise CierreRechazado(
            f"Esa solicitud era para el {dia:%d/%m/%Y}, que ya pasó. Recházala y que "
            "el vendedor pida la de mañana."
        )

    vendedor = (
        await sesion.execute(
            text(
                "SELECT u.id, u.nombre, u.almacen_id, a.tipo "
                "  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id "
                " WHERE u.id = :v"
            ),
            {"v": solicitud["vendedor_id"]},
        )
    ).mappings().one()
    if vendedor["almacen_id"] is None or vendedor["tipo"] != "camion":
        raise CierreRechazado(
            f"{vendedor['nombre']} no tiene camión asignado: no hay a dónde cargarle."
        )
    bodega = await bodega_principal(sesion)
    if bodega is None:
        raise CierreRechazado("No hay ninguna bodega activa de dónde sacar la carga.")

    pedidos = (
        await sesion.execute(
            text(
                """
                SELECT d.producto_id, d.unidad_codigo, d.cantidad, p.nombre,
                       COALESCE(u.factor, 1) AS factor
                  FROM solicitud_carga_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN producto_unidades u
                         ON u.producto_id = d.producto_id AND u.unidad_codigo = d.unidad_codigo
                 WHERE d.solicitud_id = :s
                """
            ),
            {"s": solicitud["id"]},
        )
    ).mappings().all()

    aceptadas: dict[uuid.UUID, Decimal] = {}
    for p in pedidos:
        crudo = bultos.get(str(p["producto_id"]))
        if crudo is None:
            aceptadas[p["producto_id"]] = p["cantidad"]
            continue
        if crudo.strip() in ("0", ""):
            aceptadas[p["producto_id"]] = Decimal(0)
            continue
        try:
            aceptadas[p["producto_id"]] = cantidad_base(
                _leer_bultos(crudo), Decimal(p["factor"])
            )
        except (CapturaInvalida, CantidadInvalida) as e:
            raise CierreRechazado(f"{p['nombre']}: {str(e).rstrip('.')}.") from e
    if not any(c > 0 for c in aceptadas.values()):
        raise CierreRechazado(
            "La carga quedó sin un solo renglón. Si no se le va a cargar nada, "
            "rechaza la solicitud con el motivo."
        )

    existente = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado FROM cargas "
                " WHERE vendedor_id = :v AND fecha_operativa = :d AND estado <> 'cancelada' "
                "   FOR UPDATE"
            ),
            {"v": vendedor["id"], "d": dia},
        )
    ).mappings().first()
    if existente is not None and existente["estado"] != "borrador":
        raise CierreRechazado(
            f"{vendedor['nombre']} ya tiene la carga {existente['folio']} para el "
            f"{dia:%d/%m}: solo hay una carga al día. Rechaza la solicitud o cancela "
            "esa carga si fue un error."
        )

    if existente is not None:
        # El borrador de un intento anterior, o uno que alguien empezó a mano:
        # se rehace con lo aceptado.
        carga_id, folio = existente["id"], existente["folio"]
        await sesion.execute(
            text("UPDATE cargas SET almacen_origen_id = :b WHERE id = :c"),
            {"b": bodega["id"], "c": carga_id},
        )
        await sesion.execute(
            text("DELETE FROM carga_detalle WHERE carga_id = :c"), {"c": carga_id}
        )
    else:
        consecutivo = (
            await sesion.execute(text("SELECT nextval('seq_folio_carga')"))
        ).scalar_one()
        ruta = (
            await sesion.execute(
                text("SELECT ruta_id FROM usuarios_rutas WHERE usuario_id = :v LIMIT 1"),
                {"v": vendedor["id"]},
            )
        ).scalar_one_or_none()
        carga_id, folio = uuid.uuid4(), f"CG-{consecutivo:06d}"
        await sesion.execute(
            text(
                """
                INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                                    vendedor_id, ruta_id, fecha_operativa, estado)
                VALUES (:id, :folio, :origen, :destino, :v, :r, :d, 'borrador')
                """
            ),
            {
                "id": carga_id,
                "folio": folio,
                "origen": bodega["id"],
                "destino": vendedor["almacen_id"],
                "v": vendedor["id"],
                "r": ruta,
                "d": dia,
            },
        )
    for producto_id, cantidad in aceptadas.items():
        if cantidad > 0:
            await sesion.execute(
                text(
                    "INSERT INTO carga_detalle (id, carga_id, producto_id, cantidad) "
                    "VALUES (:id, :c, :p, :cant)"
                ),
                {"id": uuid.uuid4(), "c": carga_id, "p": producto_id, "cant": cantidad},
            )

    bloqueos = await _bloqueos_para_cargar(sesion, vendedor["id"], dia)
    if bloqueos:
        # El borrador se queda: al reintentar se reutiliza.
        await sesion.commit()
        raise CierreRechazado(
            "La carga quedó en borrador y no se confirmó: " + " ".join(bloqueos)
        )

    # Lo aceptado y el estado van ANTES de mover el inventario y en su misma
    # transacción (`mover_y_confirmar` confirma al final): el teléfono recibe
    # «aceptada» con el folio, y nunca hay una carga confirmada con su solicitud
    # todavía pendiente.
    for producto_id, cantidad in aceptadas.items():
        await sesion.execute(
            text(
                "UPDATE solicitud_carga_detalle SET cantidad_aceptada = :c "
                " WHERE solicitud_id = :s AND producto_id = :p"
            ),
            {"c": cantidad, "s": solicitud["id"], "p": producto_id},
        )
    await sesion.execute(
        text(
            "UPDATE solicitudes_carga "
            "   SET estado = 'aceptada', carga_id = :c, resuelta_por = :q, "
            "       resuelta_en = now() "
            " WHERE id = :s"
        ),
        {"c": carga_id, "q": quien, "s": solicitud["id"]},
    )

    carga = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, almacen_origen_id, almacen_destino_id, "
                "       vendedor_id, fecha_operativa FROM cargas WHERE id = :c FOR UPDATE"
            ),
            {"c": carga_id},
        )
    ).mappings().one()
    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cantidad, lote, caducidad "
                "  FROM carga_detalle WHERE carga_id = :c"
            ),
            {"c": carga_id},
        )
    ).mappings().all()
    negativos = await mover_y_confirmar(
        sesion, dict(carga), renglones, quien=quien, bloqueos=[], razon_forzado=None
    )

    cambiados = sum(
        1 for p in pedidos if aceptadas[p["producto_id"]] != p["cantidad"]
    )
    aviso = (
        f"Carga {folio} confirmada para el {dia:%d/%m}: {len(renglones)} producto(s) "
        f"de {bodega['nombre']}. El teléfono del vendedor la recibe en su siguiente "
        "sincronización."
    )
    if cambiados:
        aviso += f" Se cambiaron {cambiados} renglón(es) de lo que pidió."
    if negativos:
        aviso += (
            f" Ojo: la bodega quedó con {negativos} producto(s) en negativo; es su "
            "conteo el que hay que revisar."
        )
    return aviso


# ---------------------------------------------------------------------------
# Rechazar
# ---------------------------------------------------------------------------
async def rechazar_solicitud(
    sesion, solicitud_id: uuid.UUID, *, motivo: str, quien: uuid.UUID
) -> str:
    """No se carga lo que pidió. El motivo le llega al teléfono del vendedor."""
    motivo = " ".join((motivo or "").split())[:300]
    if not motivo:
        raise CierreRechazado("Escribe por qué: al vendedor le llega el motivo.")
    fila = (
        await sesion.execute(
            text("SELECT estado FROM solicitudes_carga WHERE id = :s FOR UPDATE"),
            {"s": solicitud_id},
        )
    ).scalar_one_or_none()
    if fila is None:
        raise CierreNoExiste("Esa solicitud de carga no existe.")
    if fila != "pendiente":
        raise CierreRechazado(f"Esa solicitud ya está {fila}.")
    await sesion.execute(
        text(
            "UPDATE solicitudes_carga "
            "   SET estado = 'rechazada', motivo = :m, resuelta_por = :q, "
            "       resuelta_en = now() "
            " WHERE id = :s"
        ),
        {"m": motivo, "q": quien, "s": solicitud_id},
    )
    await sesion.commit()
    return "Solicitud rechazada. Al vendedor le llega el motivo; puede mandar otra."


def resumen_de_efectivo(corte: dict) -> str:
    """Una línea para la lista: lo que entregó contra lo que vendió en efectivo."""
    diferencia = corte["diferencia_efectivo"]
    base = f"Entrega {dinero(corte['efectivo_declarado'])} de {dinero(corte['efectivo_esperado'])}"
    if diferencia == 0:
        return base + " · cuadra"
    if diferencia < 0:
        return base + f" · faltan {dinero(-diferencia)}"
    return base + f" · sobran {dinero(diferencia)}"
