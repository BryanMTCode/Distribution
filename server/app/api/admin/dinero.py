"""Dinero: lo que vale la empresa, y la tarjeta (ADR 0002 §190).

«Quiero en el dashboard tener la cuenta del almacén, más los camiones, más la
tarjeta de débito de la empresa: las tres visiones distintas y el total. Y las
compras poder agregarlas de la tarjeta: a la tarjeta, la venta y las salidas.»

Dos pestañas, solo para el administrador (`tarjeta.ver`):

  · **Lo que vale la empresa**: la mercancía de las bodegas, la de los camiones y
    la tarjeta, cada una con su detalle, y el total.
  · **Tarjeta**: su saldo, todo lo que la movió con el saldo que dejó, y dónde se
    captura lo que no está en otro lado —con cuánto empezó, el dinero que entra o
    sale por otra cosa, y lo que dice el banco—. La compra pagada con la tarjeta
    se registra en su cuenta por pagar, eligiendo «tarjeta».

El cálculo está en `app/infra/tarjeta.py`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.admin.comun import CapturaInvalida, SesionDep, leer_dinero, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra import tarjeta
from app.infra.tarjeta import MovimientoInvalido

router = APIRouter(prefix="/panel/dinero", tags=["panel"], include_in_schema=False)

PERMISO = "tarjeta.ver"


def _a_la_tarjeta(*, error: str = "", guardado: str = "") -> RedirectResponse:
    cola = []
    if error:
        cola.append(f"error={quote(error)}")
    if guardado:
        cola.append(f"guardado={quote(guardado)}")
    destino = "/panel/dinero/tarjeta" + (f"?{'&'.join(cola)}" if cola else "")
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


@router.get("", response_class=HTMLResponse)
async def lo_que_vale(peticion: Request, actor: ActorWeb, sesion: SesionDep) -> HTMLResponse:
    actor.exigir(PERMISO)
    return render(
        peticion,
        "dinero.html",
        {"vale": await tarjeta.lo_que_vale(sesion)},
        actor=actor,
        seccion="Dinero",
    )


@router.get("/tarjeta", response_class=HTMLResponse)
async def la_tarjeta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    elegido = await periodo_pedido(sesion, periodo or "mes", desde, hasta or desde)
    todos = await tarjeta.movimientos(sesion)
    # El saldo de cada renglón se calculó con toda la historia; se enseña lo del
    # periodo, lo más nuevo arriba.
    filas = [m for m in todos if elegido.inicio <= m["cuando"].date() <= elegido.fin]
    filas.reverse()
    return render(
        peticion,
        "tarjeta.html",
        {
            "empezo": await tarjeta.empezo(sesion),
            "saldo": todos[-1]["saldo"] if todos else Decimal(0),
            "filas": filas,
            "entro": sum((m["importe"] for m in filas if m["importe"] > 0), Decimal(0)),
            "salio": sum((-m["importe"] for m in filas if m["importe"] < 0), Decimal(0)),
            "periodo": elegido,
            "periodos": PERIODOS,
            "hoy": date.today().isoformat(),
            "categorias": tarjeta.CATEGORIAS,
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Dinero",
    )


@router.post("/tarjeta/empezar")
async def empezar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    saldo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        monto = leer_dinero(saldo, campo="El saldo de la tarjeta")
        await tarjeta.empezar(sesion, saldo_hoy=monto, quien=actor.usuario_id)
    except (CapturaInvalida, MovimientoInvalido) as e:
        return _a_la_tarjeta(error=str(e))
    await sesion.commit()
    return _a_la_tarjeta(
        guardado=f"La tarjeta empieza con ${monto:,.2f}. Desde ahora se suman solos "
        "los cortes, el mayoreo y las compras pagadas con la tarjeta."
    )


@router.post("/tarjeta/movimiento")
async def movimiento(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    tipo: Annotated[str, Form()] = "",
    importe: Annotated[str, Form()] = "",
    concepto: Annotated[str, Form()] = "",
    fecha: Annotated[str, Form()] = "",
    categoria: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        monto = leer_dinero(importe, campo="El importe")
        try:
            cuando = date.fromisoformat(fecha) if fecha else date.today()
        except ValueError as e:
            raise MovimientoInvalido("La fecha no se entiende.") from e
        await tarjeta.registrar(sesion, tipo=tipo, importe=monto, concepto=concepto,
                                fecha=cuando, quien=actor.usuario_id,
                                categoria=categoria or None)
    except (CapturaInvalida, MovimientoInvalido) as e:
        return _a_la_tarjeta(error=str(e))
    await sesion.commit()
    if tipo == "salida":
        return _a_la_tarjeta(
            guardado=f"{tarjeta.CATEGORIAS[categoria]}: ${monto:,.2f} guardado."
        )
    return _a_la_tarjeta(guardado=f"Entrada de dinero de ${monto:,.2f} guardada.")


@router.post("/tarjeta/asi-esta")
async def asi_esta(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    saldo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        monto = leer_dinero(saldo, campo="Lo que dice el banco")
        diferencia = await tarjeta.asi_esta(sesion, saldo_real=monto, quien=actor.usuario_id)
    except (CapturaInvalida, MovimientoInvalido) as e:
        return _a_la_tarjeta(error=str(e))
    await sesion.commit()
    if diferencia == 0:
        return _a_la_tarjeta(guardado="Ya cuadraba con el banco: no se ajustó nada.")
    return _a_la_tarjeta(
        guardado=f"Se ajustó la diferencia ({'+' if diferencia > 0 else '−'}"
        f"${abs(diferencia):,.2f}): ahora dice ${monto:,.2f}, como el banco."
    )


@router.post("/tarjeta/{movimiento_id}/quitar")
async def quitar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    movimiento_id: uuid.UUID,
    motivo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO)
    exigir_csrf(peticion, csrf)
    try:
        await tarjeta.quitar(sesion, movimiento_id=movimiento_id, motivo=motivo,
                             quien=actor.usuario_id)
    except MovimientoInvalido as e:
        return _a_la_tarjeta(error=str(e))
    await sesion.commit()
    return _a_la_tarjeta(guardado="Se quitó. Se queda en el registro con tu nombre y el motivo.")
