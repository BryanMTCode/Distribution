"""Operaciones: todas juntas, y anular deshaciendo (ADR 0002 §119).

«Quiero una parte en el dashboard donde aparezcan todas las operaciones juntas
para borrar lo que yo necesite cuando quiera.»

Dos pantallas, sin JavaScript como todo el panel:

· la lista, con el periodo de siempre y tres filtros —tipo, quién y si está
  anulada—; cada renglón lleva a su operación;
· la operación: lo que movió, lo que anular va a hacer y lo que lo impide, y el
  formulario con el motivo. Se anula desde ahí y no desde la lista a propósito:
  quien anula ve antes lo que se va a deshacer.

Lo que de verdad se hace al anular vive en `app/infra/operaciones.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette import status

from app.api.admin.comun import SesionDep, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra import operaciones
from app.infra.operaciones import AnulacionRechazada, OperacionNoExiste

router = APIRouter(prefix="/panel/operaciones", tags=["panel"], include_in_schema=False)

PERMISO_VER = "ventas.ver_todas"
PERMISO_ANULAR = "operaciones.anular"


def _uuid(texto: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(texto) if texto else None
    except ValueError:
        return None


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    tipo: str = "",
    persona: str = "",
    estado: str = "",
    error: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO_VER)
    elegido = await periodo_pedido(sesion, periodo or "semana", desde, hasta or desde)
    tipo = tipo if tipo in operaciones.NOMBRE else ""
    estado = estado if estado in dict(operaciones.ESTADOS) else ""
    quien = _uuid(persona)
    lista = await operaciones.listar(
        sesion,
        desde=elegido.inicio,
        hasta=elegido.fin,
        tipo=tipo,
        persona=quien,
        estado=estado,
        limite=operaciones.LIMITE + 1,
    )
    return render(
        peticion,
        "operaciones.html",
        {
            "periodo": elegido,
            "periodos": PERIODOS,
            "operaciones": lista[: operaciones.LIMITE],
            "recortada": len(lista) > operaciones.LIMITE,
            "limite": operaciones.LIMITE,
            "anuladas": sum(1 for o in lista if o.anulada),
            "tipos": operaciones.TIPOS,
            "estados": operaciones.ESTADOS,
            "tipo": tipo,
            "estado": estado,
            "persona": str(quien) if quien else "",
            "personas": await operaciones.personas(sesion),
            "puede_anular": actor.puede(PERMISO_ANULAR),
            "error": error,
        },
        actor=actor,
        seccion="Operaciones",
    )


@router.get("/{tipo}/{ref}", response_class=HTMLResponse)
async def ver(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    tipo: str,
    ref: uuid.UUID,
    error: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO_VER)
    op = await operaciones.una(sesion, tipo, ref)
    if op is None:
        return _a_lista(error="Esa operación no existe.")
    plan = await operaciones.revisar(sesion, op)
    return render(
        peticion,
        "operacion.html",
        {
            "op": op,
            "plan": plan,
            "puede_anular": actor.puede(PERMISO_ANULAR),
            "error": error,
            "guardado": guardado,
        },
        actor=actor,
        seccion="Operaciones",
    )


@router.post("/{tipo}/{ref}/anular")
async def anular(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    tipo: str,
    ref: uuid.UUID,
    motivo: Annotated[str, Form()] = "",
    entiendo: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    actor.exigir(PERMISO_ANULAR)
    exigir_csrf(peticion, csrf)
    try:
        aviso = await operaciones.anular(
            sesion,
            tipo,
            ref,
            motivo=motivo,
            quien=actor.usuario_id,
            entiendo_el_corte=bool(entiendo),
        )
    except OperacionNoExiste as e:
        await sesion.rollback()
        return _a_lista(error=str(e))
    except AnulacionRechazada as e:
        await sesion.rollback()
        return _a_operacion(tipo, ref, error=str(e))
    await sesion.commit()
    return _a_operacion(tipo, ref, guardado=aviso)


def _a_lista(**que: str) -> RedirectResponse:
    cola = "&".join(f"{k}={quote(v)}" for k, v in que.items() if v)
    return RedirectResponse(
        "/panel/operaciones" + (f"?{cola}" if cola else ""),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _a_operacion(tipo: str, ref: uuid.UUID, **que: str) -> RedirectResponse:
    cola = "&".join(f"{k}={quote(v)}" for k, v in que.items() if v)
    return RedirectResponse(
        f"/panel/operaciones/{tipo}/{ref}" + (f"?{cola}" if cola else ""),
        status_code=status.HTTP_303_SEE_OTHER,
    )
