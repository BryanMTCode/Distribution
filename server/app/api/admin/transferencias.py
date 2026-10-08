"""Transferencias: el dinero de las ventas que no llegó en la mano.

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
La operación es de contado (ADR 0002 §81): toda venta se paga al entregar, en
efectivo o por transferencia. El efectivo lo cuenta el arqueo del corte. La
transferencia no viene en la bolsa del vendedor: llega al banco, y la oficina
tiene que verla ahí para que la caja del día cuadre.

Esta pantalla es SOLO para cuadrar el dinero. Antes, confirmar una transferencia
abonaba facturas y liberaba crédito; ya no hay facturas ni crédito. Confirmar
dice «el dinero está en el banco»; «no llegó» dice que hay una venta cobrada que
nadie ha visto, y deja quién lo dijo y por qué.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
Ver: `cobranza.ver`. Confirmar o marcar «no llegó»: `cobranza.confirmar`, aparte,
porque la misma mano que vigila la ruta no debe dar por buena la transferencia
de su vendedor (migración 0038). Los permisos conservan su nombre de antes.

Cargar al vendedor una que no llegó pide además `vendedores.cuenta_mover`: es la
fuga que la 0038 cerró para los cobros —cobrar en efectivo y capturarlo como
transferencia—, y sin crédito no queda nadie más a quien dejarle la deuda.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette import status

from app.api.admin.comun import SesionDep, dinero, render, texto_o_nulo
from app.api.admin.sesion_web import ActorWeb, exigir_csrf

router = APIRouter(prefix="/panel/transferencias", tags=["panel"], include_in_schema=False)

PERMISO = "cobranza.ver"
PERMISO_CONFIRMAR = "cobranza.confirmar"


async def cuantas_por_confirmar(sesion) -> dict:
    """Cuántas esperan al banco y cuánto suman. Lo usan el tablero y el arranque."""
    fila = (
        await sesion.execute(
            text(
                "SELECT count(*) AS cuantas, COALESCE(sum(total), 0) AS importe "
                "  FROM ventas "
                " WHERE pago_estado = 'por_confirmar' AND estado = 'confirmada'"
            )
        )
    ).mappings().one()
    return dict(fila)


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    guardado: str = "",
    error: str = "",
) -> HTMLResponse:
    """Las que esperan al banco, la más vieja arriba; abajo, lo resuelto reciente.

    Una transferencia que lleva cuatro días sin aparecer en el estado de cuenta ya
    no es un retraso del banco: es una pregunta para el vendedor.
    """
    actor.exigir(PERMISO)
    pendientes = (
        await sesion.execute(
            text(
                """
                SELECT v.id, v.folio_local, v.total, v.referencia_pago,
                       v.fecha_operativa, CURRENT_DATE - v.fecha_operativa AS dias,
                       c.nombre_comercial AS cliente, c.codigo AS codigo_cliente,
                       u.nombre AS vendedor
                  FROM ventas v
                  JOIN clientes c ON c.id = v.cliente_id
                  JOIN usuarios u ON u.id = v.vendedor_id
                 WHERE v.pago_estado = 'por_confirmar' AND v.estado = 'confirmada'
                 ORDER BY v.fecha_operativa, v.fecha_servidor
                 LIMIT 500
                """
            )
        )
    ).mappings().all()
    resueltas = (
        await sesion.execute(
            text(
                """
                SELECT v.id, v.folio_local, v.total, v.referencia_pago, v.pago_estado,
                       v.pago_resuelto_en, v.pago_resolucion_nota, v.fecha_operativa,
                       c.nombre_comercial AS cliente, u.nombre AS vendedor,
                       r.nombre AS resolvio
                  FROM ventas v
                  JOIN clientes c ON c.id = v.cliente_id
                  JOIN usuarios u ON u.id = v.vendedor_id
                  LEFT JOIN usuarios r ON r.id = v.pago_resuelto_por
                 WHERE v.forma_pago = 'transferencia'
                   AND v.pago_estado IN ('confirmado', 'rechazado')
                   AND v.pago_resuelto_en > now() - interval '14 days'
                 ORDER BY v.pago_resuelto_en DESC
                 LIMIT 200
                """
            )
        )
    ).mappings().all()
    return render(
        peticion,
        "transferencias.html",
        {
            "pendientes": pendientes,
            "total": sum((Decimal(f["total"]) for f in pendientes), Decimal(0)),
            "resueltas": resueltas,
            "puede_confirmar": actor.puede(PERMISO_CONFIRMAR),
            "puede_cargar": actor.puede("vendedores.cuenta_mover"),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Transferencias",
    )


@router.post("/confirmar")
async def confirmar(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    """Confirma de una vez las palomeadas: el dinero ya está en el banco.

    Va en bloque, como se concilia un estado de cuenta. Cada venta se bloquea y se
    confirma solo si SIGUE por confirmar: dos personas conciliando a la vez, o un
    doble clic, no la confirman dos veces.
    """
    actor.exigir(PERMISO_CONFIRMAR)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))

    ids: list[uuid.UUID] = []
    for valor in formulario.getlist("venta"):
        try:
            ids.append(uuid.UUID(str(valor)))
        except ValueError:
            continue
    if not ids:
        return _volver(error="No marcaste ninguna. Palomea las que ya viste en el banco.")

    ahora = datetime.now(UTC)
    confirmadas = 0
    total = Decimal(0)
    for venta_id in ids:
        fila = (
            await sesion.execute(
                text(
                    "UPDATE ventas SET pago_estado = 'confirmado', pago_resuelto_en = :ahora, "
                    "       pago_resuelto_por = :quien "
                    " WHERE id = :id AND pago_estado = 'por_confirmar' "
                    "RETURNING total"
                ),
                {"id": venta_id, "ahora": ahora, "quien": actor.usuario_id},
            )
        ).first()
        if fila is None:
            continue
        await _auditar(sesion, venta_id, "confirmar_transferencia", actor.usuario_id, None)
        confirmadas += 1
        total += Decimal(fila.total)

    await sesion.commit()
    if not confirmadas:
        return _volver(error="Ninguna seguía por confirmar: alguien más ya las resolvió.")
    return _volver(
        guardado=f"Se confirmaron {confirmadas} transferencia(s) por {dinero(total)}."
    )


@router.post("/{venta_id}/no-llego")
async def no_llego(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    venta_id: uuid.UUID,
    csrf: str = Form(""),
    motivo: str = Form(""),
    cargar_al_vendedor: str = Form(""),
):
    """El dinero no aparece en el banco. Uno por uno y con motivo: es acusar.

    Con `cargar_al_vendedor`, el importe va a su cuenta en la misma transacción,
    ligado a la venta: un doble clic no lo carga dos veces (índice único).
    """
    actor.exigir(PERMISO_CONFIRMAR)
    exigir_csrf(peticion, csrf)
    cargar = bool(cargar_al_vendedor)
    if cargar:
        actor.exigir("vendedores.cuenta_mover")
    nota = texto_o_nulo(motivo, maximo=500)
    if not nota:
        return _volver(
            error="Escribe qué pasó: «no aparece en el estado de cuenta del 8», "
            "«la referencia no existe»…"
        )
    ahora = datetime.now(UTC)
    fila = (
        await sesion.execute(
            text(
                "UPDATE ventas SET pago_estado = 'rechazado', pago_resuelto_en = :ahora, "
                "       pago_resuelto_por = :quien, pago_resolucion_nota = :nota "
                " WHERE id = :id AND pago_estado = 'por_confirmar' "
                "RETURNING folio_local, total, vendedor_id"
            ),
            {"id": venta_id, "ahora": ahora, "quien": actor.usuario_id, "nota": nota},
        )
    ).first()
    if fila is None:
        return _volver(error="Esa transferencia ya no está por confirmar.")
    if cargar:
        await sesion.execute(
            text(
                """
                INSERT INTO cuenta_vendedor
                  (vendedor_id, tipo, origen, importe, fecha, concepto, venta_id,
                   registrado_por, registrado_en)
                VALUES (:v, 'cargo', 'transferencia_no_llego', :importe, :fecha,
                        :concepto, :venta, :quien, :ahora)
                """
            ),
            {
                "v": fila.vendedor_id,
                "importe": fila.total,
                "fecha": ahora.date(),
                "concepto": f"Venta {fila.folio_local}: la transferencia no llegó. "
                f"{nota[:240]}",
                "venta": venta_id,
                "quien": actor.usuario_id,
                "ahora": ahora,
            },
        )
    await _auditar(sesion, venta_id, "transferencia_no_llego", actor.usuario_id, nota)
    await sesion.commit()
    if cargar:
        return _volver(
            guardado=f"{fila.folio_local}: marcada «no llegó» y se le cargaron "
            f"{dinero(fila.total)} al vendedor en su cuenta."
        )
    return _volver(
        guardado=f"{fila.folio_local}: marcada «no llegó» ({dinero(fila.total)}). "
        "Hay que aclararla con el vendedor y el cliente."
    )


def _volver(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/transferencias"
    if guardado:
        destino += f"?guardado={quote(guardado)}"
    elif error:
        destino += f"?error={quote(error)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


async def _auditar(sesion, venta_id, accion: str, quien, motivo: str | None) -> None:
    await sesion.execute(
        text(
            """
            INSERT INTO auditoria (entidad, entidad_id, accion, usuario_id, motivo,
                                   ocurrido_en)
            VALUES ('venta', :id, :accion, :quien, :motivo, now())
            """
        ),
        {"id": venta_id, "accion": accion, "quien": quien, "motivo": motivo},
    )
