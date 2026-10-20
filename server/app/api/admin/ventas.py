"""La venta recibida, y lo que la oficina puede hacer con ella.

────────────────────────────────────────────────────────────────────────────
CORREGIR UN DOCUMENTO QUE YA ESTÁ IMPRESO
────────────────────────────────────────────────────────────────────────────
La remisión salió de la impresora del camión, con su folio, y el cliente la tiene
en la mano. Nada de lo que pase en esta pantalla cambia ese papel.

Eso define las dos operaciones y sus límites:

**Cancelar** — la venta no debió existir: se facturó al cliente equivocado, el
cliente devolvió todo en el momento, el vendedor la capturó dos veces. La venta
pasa a `cancelada`, la mercancía vuelve al camión, y la deuda —si era a crédito—
se borra. Queda el documento de cancelación con su motivo y su nombre.

**Corregir cantidades** — se entregaron 2 cajas y el vendedor tecleó 20. Las
cantidades bajan, los importes se recalculan, y la diferencia vuelve al camión.

────────────────────────────────────────────────────────────────────────────
LAS TRES COSAS QUE ESTA PANTALLA NO HACE, Y POR QUÉ
────────────────────────────────────────────────────────────────────────────
1. **No sube cantidades.** El papel que el cliente tiene es el techo de lo que se
   entregó. «Se entregó menos» es una corrección; «se entregó más» es mercancía
   que salió del camión sin documento, y eso es una venta nueva, no una edición.
2. **No toca precios.** El vendedor no otorga descuentos (ADR 0002 §7), y
   otorgarlos desde la oficina sobre un papel ya impreso sería lo mismo con otro
   escritorio. Esta pantalla no tiene campo de precio, igual que la del vendedor.
3. **No borra la venta.** El folio está impreso, el libro mayor es append-only, y
   una venta que desaparece deja un hueco en la numeración que nadie puede
   explicar tres meses después. Se cancela, que es un estado, no una ausencia.

────────────────────────────────────────────────────────────────────────────
LO QUE BLOQUEA, Y ES LO MÁS IMPORTANTE DEL ARCHIVO
────────────────────────────────────────────────────────────────────────────
**Un día ya liquidado no se toca.** El cierre comparó el conteo físico del camión
contra los documentos del día y alguien firmó el resultado. Cambiar una venta
después mueve el inventario de un día cerrado: el faltante que se le cobró al
vendedor deja de corresponder a nada, y el papel que firmó queda explicando otra
aritmética. Si de verdad hay que anularla, se hace desde Administración →
Operaciones (ADR 0002 §119), que pide confirmar que ese corte firmado no cambia —
una decisión con nombre, no un efecto secundario de esta pantalla.

**Una venta a crédito con cobros aplicados tampoco.** El dinero ya entró y el FIFO
lo repartió sobre la cartera real. Cancelar la deuda dejaría un pago aplicado a una
factura que no existe. Primero se reversa el cobro.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import CapturaInvalida, SesionDep, dinero, render
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.workers.cola import encolar

router = APIRouter(prefix="/panel/ventas", tags=["panel"], include_in_schema=False)

PERMISO = "ventas.cancelar"
_MILESIMA = Decimal("0.001")
_CENTAVO = Decimal("0.01")


# ---------------------------------------------------------------------------
# La pista de los cambios (ADR 0002 §162)
# ---------------------------------------------------------------------------

_ACCIONES = {
    "cancelar": "Canceló la venta",
    "corregir": "Corrigió cantidades",
    "agregar": "Agregó artículos",
}


def describir_cambio(
    accion: str, antes: dict | None, despues: dict | None, nombres: dict[str, str]
) -> tuple[str, bool]:
    """Un renglón del historial en palabras, y si hay que mirarlo con cuidado.

    Lo que más hay que mirar es lo que el vendedor cambió desde el teléfono
    DESPUÉS de entregar el ticket: el cliente se quedó con un papel que ya no
    dice lo mismo que el sistema.
    """
    antes, despues = antes or {}, despues or {}
    partes = [_ACCIONES.get(accion, accion)]
    if accion == "agregar":
        agregadas = [
            f"{_cantidad(a.get('cantidad'))} {a.get('unidad') or ''} "
            f"{nombres.get(a.get('producto_id'), 'producto')}".strip()
            for a in despues.get("agregadas") or []
        ]
        if agregadas:
            partes[0] += ": " + ", ".join(agregadas)
    if antes.get("total") is not None and despues.get("total") is not None:
        partes.append(f"total {dinero(antes['total'])} → {dinero(despues['total'])}")
    desde_app = despues.get("desde") == "app"
    if desde_app:
        partes.append("desde el teléfono")
        impreso = despues.get("ticket_impreso")
        if impreso:
            copias = despues.get("reimpresiones") or 0
            partes.append(
                "con el ticket ya impreso"
                + (f" ({copias} copia{'s' if copias != 1 else ''})" if copias else "")
            )
        elif impreso is False:
            partes.append("sin haber impreso el ticket")
        distancia = despues.get("distancia_cliente_m")
        if distancia is not None:
            partes.append(f"a {distancia} m de la tienda")
        elif despues.get("lat") is None:
            partes.append("sin GPS")
    return " · ".join(partes), bool(desde_app and despues.get("ticket_impreso"))


def _cantidad(texto) -> str:
    try:
        valor = Decimal(str(texto))
    except Exception:  # noqa: BLE001 — un dato viejo raro no tumba la pantalla
        return str(texto or "")
    return f"{valor.normalize():f}"


async def _nombres_de_productos(sesion, filas) -> dict[str, str]:
    ids = {
        a.get("producto_id")
        for f in filas
        for a in ((f["datos_despues"] or {}).get("agregadas") or [])
        if a.get("producto_id")
    }
    if not ids:
        return {}
    return {
        str(r["id"]): r["nombre"]
        for r in (
            await sesion.execute(
                text("SELECT id, nombre FROM productos WHERE id::text = ANY(:ids)"),
                {"ids": list(ids)},
            )
        ).mappings()
    }


async def _historial(sesion, venta_id: uuid.UUID) -> list[dict]:
    filas = (
        await sesion.execute(
            text(
                """
                SELECT a.accion, a.motivo, a.datos_antes, a.datos_despues,
                       a.ocurrido_en, u.nombre AS quien
                  FROM auditoria a
                  LEFT JOIN usuarios u ON u.id = a.usuario_id
                 WHERE a.entidad = 'venta' AND a.entidad_id = :v
                 ORDER BY a.ocurrido_en, a.id
                """
            ),
            {"v": venta_id},
        )
    ).mappings().all()
    nombres = await _nombres_de_productos(sesion, filas)
    historial = []
    for f in filas:
        que, alerta = describir_cambio(f["accion"], f["datos_antes"], f["datos_despues"], nombres)
        historial.append(
            {"cuando": f["ocurrido_en"], "quien": f["quien"], "que": que,
             "motivo": f["motivo"], "alerta": alerta}
        )
    return historial


@router.get("/cambios", response_class=HTMLResponse)
async def cambios_de_los_vendedores(
    peticion: Request, actor: ActorWeb, sesion: SesionDep, dias: int = 7
) -> HTMLResponse:
    """Lo que los vendedores cancelaron o agregaron desde el teléfono (§162).

    Es la pista que pidió la dirección: cancelar o agregar es legítimo —el
    vendedor se equivoca—, y también es por donde se escaparía un cobro. Primero
    las que se cambiaron con el ticket ya impreso.
    """
    actor.exigir("ventas.ver_todas")
    dias = min(max(dias, 1), 90)
    filas = (
        await sesion.execute(
            text(
                """
                SELECT a.accion, a.motivo, a.datos_antes, a.datos_despues,
                       a.ocurrido_en, v.id AS venta_id, v.folio_local, v.estado,
                       v.total, c.nombre_comercial AS cliente, u.nombre AS vendedor
                  FROM auditoria a
                  JOIN ventas v ON v.id = a.entidad_id
                  JOIN clientes c ON c.id = v.cliente_id
                  JOIN usuarios u ON u.id = v.vendedor_id
                 WHERE a.entidad = 'venta'
                   AND a.accion IN ('cancelar', 'agregar')
                   AND a.usuario_id = v.vendedor_id
                   AND a.ocurrido_en >= now() - make_interval(days => :dias)
                 ORDER BY a.ocurrido_en DESC
                 LIMIT 300
                """
            ),
            {"dias": dias},
        )
    ).mappings().all()
    nombres = await _nombres_de_productos(sesion, filas)
    cambios = []
    for f in filas:
        que, alerta = describir_cambio(f["accion"], f["datos_antes"], f["datos_despues"], nombres)
        cambios.append({**f, "que": que, "alerta": alerta})
    return render(
        peticion,
        "ventas_cambios.html",
        {
            "cambios": cambios,
            "dias": dias,
            "con_ticket": sum(1 for c in cambios if c["alerta"]),
        },
        actor=actor,
        seccion="Ventas",
    )


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


@router.get("/{venta_id}", response_class=HTMLResponse)
async def detalle(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    venta_id: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir("ventas.ver_todas")

    cabecera = await _cabecera(sesion, venta_id)
    if cabecera is None:
        return RedirectResponse("/panel/ventas", status_code=303)

    partidas = (
        await sesion.execute(
            text(
                """
                SELECT vp.id, vp.linea, vp.producto_id, vp.unidad_codigo,
                       vp.factor_unidad, vp.cantidad, vp.cantidad_base,
                       vp.precio_unitario, vp.importe,
                       p.sku, p.nombre, p.unidad_base
                  FROM venta_partidas vp
                  JOIN productos p ON p.id = vp.producto_id
                 WHERE vp.venta_id = :v
                 ORDER BY vp.linea
                """
            ),
            {"v": venta_id},
        )
    ).mappings().all()

    bloqueos = await _bloqueos(sesion, cabecera)

    return render(
        peticion,
        "venta_detalle.html",
        {
            "v": cabecera,
            "partidas": partidas,
            "bloqueos": bloqueos,
            "historial": await _historial(sesion, venta_id),
            # Los formularios aparecen solo cuando de verdad se puede guardar. Un
            # botón que se puede tocar y luego explica que no se podía es peor que
            # un botón ausente con su razón al lado — y en esta pantalla el botón
            # cancela una venta.
            "abierta_para_editar": (
                cabecera["estado"] == "confirmada"
                and actor.puede(PERMISO)
                and not bloqueos
            ),
            "puede_editar": actor.puede(PERMISO),
            "total_texto": dinero(cabecera["total"]),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Ventas",
    )


# ---------------------------------------------------------------------------
# Cancelar
# ---------------------------------------------------------------------------


@router.post("/{venta_id}/cancelar")
async def cancelar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    venta_id: uuid.UUID,
    motivo: Annotated[str, Form()] = "",
    reingresa_stock: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Cancela la venta completa y devuelve la mercancía al camión.

    `reingresa_stock` viene de la tabla `ventas_cancelaciones`, que lo previó desde
    la migración 0005, y distingue dos cancelaciones que se ven iguales: la venta
    que no ocurrió —la mercancía nunca salió del camión, o volvió— y la que se
    facturó al cliente equivocado, donde la mercancía SÍ se entregó y lo que sigue
    es registrarla a nombre de quien se la llevó. En la segunda, devolverla al
    camión la duplicaría.
    """
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)

    razon = (motivo or "").strip()
    if len(razon) < 10:
        return _volver(
            venta_id,
            error="Escribe el motivo de la cancelación, con al menos 10 "
            "caracteres: es lo que alguien va a leer dentro de seis meses.",
        )

    cabecera = await _cabecera(sesion, venta_id, para_escribir=True)
    if cabecera is None:
        return RedirectResponse("/panel/ventas", status_code=303)
    if cabecera["estado"] != "confirmada":
        return _volver(venta_id, error="Esta venta ya estaba cancelada.")

    bloqueos = await _bloqueos(sesion, cabecera)
    if bloqueos:
        return _volver(venta_id, error="No se puede cancelar: " + " ".join(bloqueos))

    devuelve = bool(reingresa_stock)
    await cancelar_venta(
        sesion, cabecera, motivo=razon, reingresa=devuelve, quien=actor.usuario_id
    )
    await sesion.commit()

    aviso = f"Venta {cabecera['folio_local']} cancelada. "
    aviso += (
        "La mercancía volvió al camión y el teléfono lo recibe en la siguiente "
        "sincronización."
        if devuelve
        else "La mercancía NO volvió al camión: sigue fuera, como se indicó."
    )
    return _volver(venta_id, guardado=aviso)


async def cancelar_venta(
    sesion,
    cabecera,
    *,
    motivo: str,
    reingresa: bool,
    quien: uuid.UUID,
    rastro: dict | None = None,
) -> None:
    """Lo que escribe cancelar una venta. No valida ni hace commit.

    Lo usan esta pantalla, Operaciones (ADR 0002 §119) y el teléfono del vendedor
    (§161): los tres cancelan igual —el mismo documento, la misma devolución al
    camión, el mismo delta al teléfono—, y cada uno decide antes qué la bloquea.
    [rastro] es lo que el teléfono sabe de ese momento —su hora, si el ticket ya
    se había impreso, dónde estaba— y va al historial.
    """
    venta_id = cabecera["id"]
    ahora = datetime.now(UTC)

    cancelacion_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO ventas_cancelaciones
              (id, venta_id, motivo, reingresa_stock, autorizado_por, usuario_id,
               fecha_servidor)
            VALUES (:id, :v, :motivo, :devuelve, :quien, :quien, :ahora)
            """
        ),
        {
            "id": cancelacion_id,
            "v": venta_id,
            "motivo": motivo[:600],
            "devuelve": reingresa,
            "quien": quien,
            "ahora": ahora,
        },
    )

    if reingresa:
        partidas = (
            await sesion.execute(
                text(
                    "SELECT producto_id, sum(cantidad_base) AS base "
                    "  FROM venta_partidas WHERE venta_id = :v GROUP BY producto_id"
                ),
                {"v": venta_id},
            )
        ).mappings().all()
        for p in partidas:
            await _devolver_al_camion(
                sesion,
                almacen=cabecera["almacen_id"],
                producto=p["producto_id"],
                cantidad=Decimal(p["base"]),
                documento_tipo="venta_cancelada",
                documento_id=cancelacion_id,
                quien=quien,
                ahora=ahora,
            )

    # La deuda se borra, no se marca: una cuenta por cobrar de una venta que no
    # existe no es cobrable, y dejarla «incobrable» la haría aparecer en la
    # cartera por antigüedad como si alguien tuviera que perseguirla. El delta de
    # cartera (migración 0011) sale solo del DELETE y le baja el saldo al
    # teléfono.
    await sesion.execute(
        text("DELETE FROM cuentas_por_cobrar WHERE venta_id = :v"), {"v": venta_id}
    )

    # Este UPDATE es el que publica el delta al teléfono (migración 0031).
    await sesion.execute(
        text("UPDATE ventas SET estado = 'cancelada' WHERE id = :v"), {"v": venta_id}
    )

    await _auditar(
        sesion,
        venta_id,
        accion="cancelar",
        quien=quien,
        motivo=motivo,
        antes={
            "estado": "confirmada",
            "total": str(cabecera["total"]),
            "folio_local": cabecera["folio_local"],
        },
        despues={"estado": "cancelada", "reingresa_stock": reingresa, **(rastro or {})},
        ahora=ahora,
    )
    await _recalcular(sesion, cabecera["fecha_operativa"])


# ---------------------------------------------------------------------------
# Corregir cantidades
# ---------------------------------------------------------------------------


@router.post("/{venta_id}/corregir")
async def corregir(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    venta_id: uuid.UUID,
):
    """Baja las cantidades de una venta y devuelve la diferencia al camión.

    Los campos vienen como `cantidad_<id de la partida>`, así que se leen del
    formulario crudo. Un campo **vacío significa «déjala como está»**, al contrario
    que en el conteo de la liquidación: aquí no se está contando un camión, se está
    corrigiendo un renglón, y los demás renglones del documento no deberían cambiar
    porque alguien no los volvió a escribir.
    """
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))

    razon = str(formulario.get("motivo") or "").strip()
    if len(razon) < 10:
        return _volver(
            venta_id,
            error="Escribe el motivo de la corrección, con al menos 10 "
            "caracteres: esta venta va a dejar de coincidir con su papel impreso.",
        )

    cabecera = await _cabecera(sesion, venta_id, para_escribir=True)
    if cabecera is None:
        return RedirectResponse("/panel/ventas", status_code=303)
    if cabecera["estado"] != "confirmada":
        return _volver(venta_id, error="Una venta cancelada ya no se corrige.")

    bloqueos = await _bloqueos(sesion, cabecera)
    if bloqueos:
        return _volver(venta_id, error="No se puede corregir: " + " ".join(bloqueos))

    partidas = (
        await sesion.execute(
            text(
                "SELECT vp.id, vp.linea, vp.producto_id, vp.unidad_codigo, "
                "       vp.factor_unidad, vp.cantidad, vp.cantidad_base, "
                "       vp.precio_unitario, vp.tasa_iva, vp.importe, p.nombre "
                "  FROM venta_partidas vp "
                "  JOIN productos p ON p.id = vp.producto_id "
                " WHERE vp.venta_id = :v ORDER BY vp.linea"
            ),
            {"v": venta_id},
        )
    ).mappings().all()

    try:
        cambios = _leer_correccion(partidas, formulario)
    except CapturaInvalida as e:
        return _volver(venta_id, error=str(e))

    if not cambios:
        return _volver(venta_id, error="No cambiaste ninguna cantidad.")

    quedan = [
        p for p in partidas
        if _nueva_cantidad(p, cambios) > 0
    ]
    if not quedan:
        return _volver(
            venta_id,
            error="Si todos los renglones quedan en cero, lo que corresponde es "
            "cancelar la venta: así queda el documento de cancelación con su "
            "motivo, en vez de una venta de cero pesos.",
        )

    ahora = datetime.now(UTC)
    antes = [
        {
            "linea": p["linea"],
            "producto": p["nombre"],
            "cantidad": str(p["cantidad"]),
            "importe": str(p["importe"]),
        }
        for p in partidas
    ]

    # ------------------------------------------------------------------
    # Las partidas PRIMERO, el encabezado DESPUÉS.
    # ------------------------------------------------------------------
    # El disparador que publica la venta al teléfono cuelga del UPDATE sobre
    # `ventas` y lee las partidas en ese momento. Si el encabezado se actualizara
    # antes, el delta viajaría con las cantidades viejas y el teléfono devolvería
    # al camión una diferencia de cero.
    nuevo_subtotal = Decimal(0)
    nuevos_impuestos = Decimal(0)
    for p in partidas:
        nueva = _nueva_cantidad(p, cambios)
        factor = Decimal(p["factor_unidad"])
        base_nueva = (nueva * factor).quantize(_MILESIMA)
        base_vieja = Decimal(p["cantidad_base"])
        importe = (nueva * Decimal(p["precio_unitario"])).quantize(_CENTAVO)
        iva = (importe * Decimal(p["tasa_iva"])).quantize(_CENTAVO)

        if nueva == 0:
            await sesion.execute(
                text("DELETE FROM venta_partidas WHERE id = :id"), {"id": p["id"]}
            )
        else:
            await sesion.execute(
                text(
                    "UPDATE venta_partidas "
                    "   SET cantidad = :cant, cantidad_base = :base, importe = :imp "
                    " WHERE id = :id"
                ),
                {"cant": nueva, "base": base_nueva, "imp": importe, "id": p["id"]},
            )
            nuevo_subtotal += importe
            nuevos_impuestos += iva

        devuelve = base_vieja - base_nueva
        if devuelve > 0:
            await _devolver_al_camion(
                sesion,
                almacen=cabecera["almacen_id"],
                producto=p["producto_id"],
                cantidad=devuelve,
                documento_tipo="venta_corregida",
                documento_id=venta_id,
                quien=actor.usuario_id,
                ahora=ahora,
            )

    nuevo_total = nuevo_subtotal - Decimal(cabecera["descuento"]) + nuevos_impuestos

    # La deuda baja con la venta. Si ya había cobros aplicados por MÁS de lo que
    # queda, `_bloqueos` lo impidió arriba: aquí solo puede bajar sin dejar la
    # cuenta sobrepagada, que es lo que el CHECK `pago_no_excede_original` exige.
    if cabecera["tipo"] == "credito":
        await sesion.execute(
            text(
                "UPDATE cuentas_por_cobrar "
                "   SET importe_original = :total, actualizado_en = :ahora, "
                "       estado = CASE WHEN importe_pagado >= :total THEN 'liquidada' "
                "                     WHEN importe_pagado > 0 THEN 'parcial' "
                "                     ELSE 'abierta' END "
                " WHERE venta_id = :v"
            ),
            {"total": nuevo_total, "ahora": ahora, "v": venta_id},
        )

    await sesion.execute(
        text(
            "UPDATE ventas "
            "   SET subtotal = :sub, impuestos = :iva, total = :total, "
            "       corregida_en = :ahora, corregida_por = :quien, "
            "       correccion_motivo = :motivo "
            " WHERE id = :v"
        ),
        {
            "sub": nuevo_subtotal,
            "iva": nuevos_impuestos,
            "total": nuevo_total,
            "ahora": ahora,
            "quien": actor.usuario_id,
            "motivo": razon[:600],
            "v": venta_id,
        },
    )

    await _auditar(
        sesion,
        venta_id,
        accion="corregir",
        quien=actor.usuario_id,
        motivo=razon,
        antes={
            "total": str(cabecera["total"]),
            "folio_local": cabecera["folio_local"],
            "partidas": antes,
        },
        despues={"total": str(nuevo_total)},
        ahora=ahora,
    )
    await _recalcular(sesion, cabecera["fecha_operativa"])
    await sesion.commit()

    return _volver(
        venta_id,
        guardado=(
            f"Venta {cabecera['folio_local']} corregida: ahora es "
            f"{dinero(nuevo_total)} (antes {dinero(cabecera['total'])}). La "
            "diferencia volvió al camión."
        ),
    )


# ---------------------------------------------------------------------------
# Dar por revisada
# ---------------------------------------------------------------------------


@router.post("/{venta_id}/revisada")
async def marcar_revisada(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    venta_id: uuid.UUID,
    nota: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Da por revisada una venta marcada, sin corregir nada.

    §0.1 dice que el servidor marca y no rechaza. La otra mitad de ese principio es
    que alguien mire lo marcado: una bandera que nadie baja deja de significar algo
    y la lista de «por revisar» se vuelve ruido que se ignora completo.

    La nota se agrega a `revision_motivos` junto al motivo original en vez de
    borrarlo —el mismo sello que usa la cobranza—: por qué se marcó es parte del
    historial, y perderlo haría que la misma situación se investigara desde cero el
    mes que viene.
    """
    actor.exigir("ventas.ver_todas")
    exigir_csrf(peticion, csrf)

    limpia = (nota or "").strip()
    sello = f"revisada_por:{actor.usuario_id}"
    if limpia:
        sello = f"{sello}:{limpia[:200]}"

    await sesion.execute(
        text(
            "UPDATE ventas "
            "   SET requiere_revision = false, "
            "       revision_motivos = revision_motivos || :sello "
            " WHERE id = :v AND requiere_revision"
        ),
        {"v": venta_id, "sello": [sello]},
    )
    await sesion.commit()
    return _volver(venta_id, guardado="Queda registrado que la revisaste.")


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


async def _cabecera(sesion, venta_id: uuid.UUID, *, para_escribir: bool = False):
    return (
        await sesion.execute(
            text(
                "SELECT v.*, c.nombre_comercial AS cliente, c.codigo AS cliente_codigo, "
                "       u.nombre AS vendedor, a.nombre AS camion, "
                "       q.nombre AS corregida_por_nombre, "
                "       x.importe_pagado, x.importe_original, "
                "       k.motivo AS cancelacion_motivo, k.reingresa_stock, "
                "       w.nombre AS cancelada_por_nombre, k.fecha_servidor AS cancelada_en "
                "  FROM ventas v "
                "  JOIN clientes c ON c.id = v.cliente_id "
                "  JOIN usuarios u ON u.id = v.vendedor_id "
                "  JOIN almacenes a ON a.id = v.almacen_id "
                "  LEFT JOIN usuarios q ON q.id = v.corregida_por "
                "  LEFT JOIN cuentas_por_cobrar x ON x.venta_id = v.id "
                "  LEFT JOIN ventas_cancelaciones k ON k.venta_id = v.id "
                "  LEFT JOIN usuarios w ON w.id = k.usuario_id "
                f" WHERE v.id = :v{' FOR UPDATE OF v' if para_escribir else ''}"
            ),
            {"v": venta_id},
        )
    ).mappings().first()


async def _bloqueos(sesion, cabecera) -> list[str]:
    """Lo que impide tocar esta venta. Lista vacía = se puede.

    Se calcula también al pintar la pantalla, no solo al guardar: un botón que se
    puede tocar y luego explica que no se podía es peor que un botón ausente con su
    razón al lado.
    """
    bloqueos: list[str] = []

    # El día liquidado. Se busca por la carga de la venta y, si no trae carga, por
    # el día y el vendedor: con el camión rodante una venta puede salir sin carga
    # nueva, y ese día también se liquida.
    cerrada = (
        await sesion.execute(
            text(
                """
                SELECT l.folio
                  FROM liquidaciones l
                  JOIN cargas g ON g.id = l.carga_id
                 WHERE l.estado = 'cerrada'
                   AND (l.carga_id = :carga
                        OR (g.vendedor_id = :vendedor
                            AND l.fecha_operativa = :dia))
                 LIMIT 1
                """
            ),
            {
                "carga": cabecera["carga_id"],
                "vendedor": cabecera["vendedor_id"],
                "dia": cabecera["fecha_operativa"],
            },
        )
    ).scalar_one_or_none()
    if cerrada:
        bloqueos.append(
            f"el día de esta venta ya se liquidó ({cerrada}). Ese cierre comparó el "
            "conteo del camión contra los documentos del día y alguien firmó el "
            "resultado; cambiar la venta ahora dejaría ese papel explicando otra "
            "aritmética. Si de verdad hay que anularla, hazlo desde Administración → "
            "Operaciones: ahí se anula diciendo que ese corte firmado no cambia."
        )

    pagado = cabecera["importe_pagado"]
    if pagado is not None and Decimal(pagado) > 0:
        bloqueos.append(
            f"esta venta a crédito ya tiene {dinero(pagado)} cobrados y aplicados. "
            "Reversa el cobro primero: si no, quedaría un pago aplicado a una "
            "factura que ya no existe."
        )

    return bloqueos


def _nueva_cantidad(partida, cambios: dict) -> Decimal:
    return cambios.get(partida["id"], Decimal(partida["cantidad"]))


def _leer_correccion(partidas, formulario) -> dict:
    """Las cantidades nuevas, validadas contra las del papel.

    Devuelve solo las que CAMBIAN: así `corregir` puede decir «no cambiaste nada»
    en vez de reescribir la venta idéntica y publicar un delta vacío.
    """
    cambios: dict = {}
    for p in partidas:
        crudo = formulario.get(f"cantidad_{p['id']}")
        if crudo is None or str(crudo).strip() == "":
            continue
        texto = str(crudo).strip().replace(",", "")
        try:
            valor = Decimal(texto)
        except ArithmeticError as e:
            raise CapturaInvalida(f"«{crudo}» no es una cantidad.") from e
        if not valor.is_finite() or valor < 0:
            raise CapturaInvalida(
                f"La cantidad del renglón {p['linea']} no puede ser negativa."
            )
        valor = valor.quantize(_MILESIMA)
        actual = Decimal(p["cantidad"])
        if valor > actual:
            raise CapturaInvalida(
                f"El renglón {p['linea']} ({p['nombre']}) dice {actual} en el papel "
                f"y escribiste {valor}. Las cantidades solo pueden BAJAR: entregar "
                "más de lo que dice la remisión es mercancía que salió del camión "
                "sin documento, y eso es una venta nueva, no una corrección."
            )
        if valor != actual:
            cambios[p["id"]] = valor
    return cambios


async def _devolver_al_camion(
    sesion,
    *,
    almacen: uuid.UUID,
    producto: uuid.UUID,
    cantidad: Decimal,
    documento_tipo: str,
    documento_id: uuid.UUID,
    quien: uuid.UUID,
    ahora: datetime,
) -> None:
    """La mercancía regresa: movimiento en el libro mayor y caché de existencias.

    Tipo `devolucion`, que el esquema define como «(entra) → camión» desde la
    migración 0004. No es un `ajuste`: un ajuste es una corrección de inventario sin
    documento que la explique, y esto tiene uno — la venta que dejó de existir.
    """
    await sesion.execute(
        text(
            """
            INSERT INTO movimientos_inventario
              (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
               documento_tipo, documento_id, usuario_id, fecha_servidor)
            VALUES ('devolucion', NULL, :a, :p, :cant, :dtipo, :doc, :quien, :ahora)
            """
        ),
        {
            "a": almacen,
            "p": producto,
            "cant": cantidad,
            "dtipo": documento_tipo,
            "doc": documento_id,
            "quien": quien,
            "ahora": ahora,
        },
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
        {"a": almacen, "p": producto, "cant": cantidad, "ahora": ahora},
    )


async def _auditar(
    sesion,
    venta_id: uuid.UUID,
    *,
    accion: str,
    quien: uuid.UUID,
    motivo: str,
    antes: dict,
    despues: dict,
    ahora: datetime,
) -> None:
    """El antes y el después, en la misma transacción que el cambio.

    Es lo único que puede explicar, meses después, por qué el sistema y el papel
    del cliente no dicen lo mismo. Si el commit se cae, no queda constancia de algo
    que no pasó.
    """
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria
              (entidad, entidad_id, accion, usuario_id, motivo,
               datos_antes, datos_despues, ocurrido_en)
            VALUES ('venta', :id, :accion, :quien, :motivo,
                    CAST(:antes AS jsonb), CAST(:despues AS jsonb), :ahora)
            """
        ),
        {
            "id": venta_id,
            "accion": accion,
            "quien": quien,
            "motivo": motivo,
            "antes": json.dumps(antes, ensure_ascii=False),
            "despues": json.dumps(despues, ensure_ascii=False),
            "ahora": ahora,
        },
    )


async def _recalcular(sesion, fecha_operativa) -> None:
    """El tablero y el laboratorio cambiaron: una venta menos, o menos importe.

    Con la `clave_unica` por día, corregir cinco ventas encola UN refresco y no
    cinco. Y va en la misma transacción que el cambio: si se deshace, el job no
    queda pidiendo recalcular algo que no pasó.
    """
    await encolar(
        sesion,
        "recalcular_tablero",
        {"motivo": "venta_editada", "fecha": str(fecha_operativa)},
        clave_unica="recalcular_tablero",
        retraso=timedelta(seconds=30),
    )
    await encolar(
        sesion,
        "refrescar_analitica",
        {"motivo": "venta_editada", "fecha": str(fecha_operativa)},
        clave_unica=f"refrescar_analitica:{fecha_operativa}",
        retraso=timedelta(minutes=1),
    )


def _volver(venta_id, *, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = f"/panel/ventas/{venta_id}" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
