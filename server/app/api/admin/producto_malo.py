"""Inventario → Producto malo: lo que cada vendedor tiene que regresar (ADR 0002 §196).

«Si Federico tiene 4 malas, quiero llevar control de esas para que las regrese al
almacén.» Lo malo de cada cambio físico queda a nombre del vendedor hasta que:

  · la oficina lo **recibe** en la bodega —entra al almacén «Producto malo», de
    donde después se tira o se le regresa al proveedor con una salida—, o
  · se le **carga a su cuenta**, a costo, con un botón. Nada se le cobra solo.

La cuenta vive en `app/infra/producto_malo.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.admin.comun import (
    MAXIMO_CAMPOS_DE_RENGLONES,
    CapturaInvalida,
    SesionDep,
    leer_entero,
    render,
)
from app.api.admin.cuenta_vendedores import PERMISO_MOVER as PERMISO_CARGAR
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra import producto_malo

router = APIRouter(
    prefix="/panel/inventario/producto-malo", tags=["panel"], include_in_schema=False
)

PERMISO_VER = "inventario.ver"
# Recibir mueve inventario: el mismo permiso que mover mercancía.
PERMISO_RECIBIR = "inventario.ajustar"

# Más piezas que estas en un renglón es un cero de más.
MAXIMO_PIEZAS = 100000


def _a_la_pantalla(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/inventario/producto-malo"
    if guardado:
        destino += f"?guardado={quote(guardado)}"
    elif error:
        destino += f"?error={quote(error)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


@router.get("", response_class=HTMLResponse)
async def pantalla(
    peticion: Request, actor: ActorWeb, sesion: SesionDep, guardado: str = "", error: str = ""
) -> HTMLResponse:
    actor.exigir(PERMISO_VER)
    renglones = await producto_malo.por_regresar(sesion)
    # Por vendedor, en el orden de la consulta: lo que le falta y lo que ya no.
    vendedores: dict = {}
    for r in renglones:
        v = vendedores.setdefault(
            r["vendedor_id"],
            {"id": r["vendedor_id"], "nombre": r["vendedor"], "renglones": [], "falta": Decimal(0)},
        )
        v["renglones"].append(r)
        v["falta"] += max(Decimal(r["pendientes"]), Decimal(0))
    cambios = await producto_malo.cambios(sesion)
    for c in cambios:
        if c["vendedor_id"] in vendedores:
            vendedores[c["vendedor_id"]].setdefault("cambios", []).append(c)
    con_pendiente = [v for v in vendedores.values() if v["falta"] > 0]
    return render(
        peticion,
        "producto_malo.html",
        {
            "con_pendiente": con_pendiente,
            "sin_pendiente": [v for v in vendedores.values() if v["falta"] <= 0],
            "total_falta": sum((v["falta"] for v in con_pendiente), Decimal(0)),
            "en_la_bodega": await producto_malo.en_la_bodega(sesion),
            "resuelto": await producto_malo.resuelto(sesion),
            "puede_recibir": actor.puede(PERMISO_RECIBIR),
            "puede_cargar": actor.puede(PERMISO_CARGAR),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Inventario",
    )


async def _lo_capturado(peticion: Request) -> tuple[uuid.UUID, dict, bool]:
    """El vendedor, cuántas piezas de cada artículo (`c_<producto_id>`) y si
    marcó «Sí, cargárselo»."""
    formulario = await peticion.form(max_fields=MAXIMO_CAMPOS_DE_RENGLONES)
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    try:
        vendedor_id = uuid.UUID(str(formulario.get("vendedor_id", "")))
    except ValueError as e:
        raise CapturaInvalida("Falta de qué vendedor es.") from e
    cantidades = {}
    for campo, valor in formulario.multi_items():
        if not campo.startswith("c_"):
            continue
        try:
            producto_id = uuid.UUID(campo[2:])
        except ValueError:
            continue
        piezas = leer_entero(str(valor), campo="Las piezas", maximo=MAXIMO_PIEZAS)
        if piezas > 0:
            cantidades[producto_id] = Decimal(piezas)
    return vendedor_id, cantidades, formulario.get("confirmo_cargo") == "1"


@router.post("/recibir")
async def recibir(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    """Lo que el vendedor entregó en la bodega."""
    actor.exigir(PERMISO_VER)
    actor.exigir(PERMISO_RECIBIR)
    try:
        vendedor_id, cantidades, _ = await _lo_capturado(peticion)
        folio = await producto_malo.recibir(
            sesion, vendedor_id=vendedor_id, cantidades=cantidades, quien=actor.usuario_id
        )
    except (CapturaInvalida, producto_malo.NoSePuede) as e:
        await sesion.rollback()
        return _a_la_pantalla(error=str(e))
    await sesion.commit()
    piezas = sum(cantidades.values(), Decimal(0))
    return _a_la_pantalla(
        guardado=f"Recibidas {piezas.normalize():f} pieza(s) en «{producto_malo.NOMBRE}» "
        f"({folio}). Si fue un error, se elimina en Operaciones."
    )


@router.post("/cargar")
async def cargar(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    """Lo que no entregó, a su cuenta y a costo."""
    actor.exigir(PERMISO_VER)
    actor.exigir(PERMISO_CARGAR)
    try:
        vendedor_id, cantidades, confirmo = await _lo_capturado(peticion)
        if not confirmo:
            # Recibir y cargar comparten el formulario: la casilla es lo que
            # separa un cobro de un clic en el botón equivocado.
            raise CapturaInvalida("Para cargárselo a su cuenta, marca «Sí, cargárselo».")
        importe = await producto_malo.cargar(
            sesion, vendedor_id=vendedor_id, cantidades=cantidades, quien=actor.usuario_id
        )
    except (CapturaInvalida, producto_malo.NoSePuede) as e:
        await sesion.rollback()
        return _a_la_pantalla(error=str(e))
    await sesion.commit()
    return _a_la_pantalla(
        guardado=f"Se le cargaron ${importe:,.2f} a su cuenta, a costo. Si fue un error, "
        "se condona en su cuenta."
    )
