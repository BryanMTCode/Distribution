"""Cortes y cargas por aceptar: el cierre de cada vendedor, en el panel.

El vendedor hace su corte y pide la carga de mañana desde el teléfono. Aquí la
oficina los revisa juntos y los acepta: el corte se cierra —lo que falte va a la
cuenta del vendedor— y la carga se crea y se confirma (ADR 0002 §82). Todo lo
que decide algo está en `app/infra/cierre_del_vendedor.py`, el mismo que usa la
app del gerente (`/v1/cierres`).

Ver exige `inventario.cargar`; aceptar exige además `inventario.liquidar`.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette import status

from app.api.admin.comun import SesionDep, render
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra.cierre_del_vendedor import (
    CierreNoExiste,
    CierreRechazado,
    aceptar,
    cierres,
    rechazar_solicitud,
    resumen_de_efectivo,
)

router = APIRouter(prefix="/panel/cierres", tags=["panel"], include_in_schema=False)

PERMISO = "inventario.cargar"
PERMISO_CERRAR = "inventario.liquidar"


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request, actor: ActorWeb, sesion: SesionDep, guardado: str = "", error: str = ""
) -> HTMLResponse:
    actor.exigir(PERMISO)
    datos = await cierres(sesion)
    for c in datos["pendientes"] + datos["recientes"]:
        if c["corte"]:
            c["corte"]["resumen"] = resumen_de_efectivo(c["corte"])
    return render(
        peticion,
        "cierres.html",
        {
            "pendientes": datos["pendientes"],
            "recientes": datos["recientes"],
            "puede_cerrar": actor.puede(PERMISO_CERRAR),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Cortes y cargas por aceptar",
    )


@router.post("/aceptar")
async def aceptar_cierre(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    actor.exigir(PERMISO)
    actor.exigir(PERMISO_CERRAR)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    bultos = {
        str(clave)[len("bultos_"):]: str(valor)
        for clave, valor in formulario.items()
        if str(clave).startswith("bultos_") and str(valor).strip() != ""
    }
    try:
        aviso = await aceptar(
            sesion,
            corte_id=_uuid(formulario.get("corte_id")),
            solicitud_id=_uuid(formulario.get("solicitud_id")),
            quien=actor.usuario_id,
            bultos=bultos,
        )
    except (CierreNoExiste, CierreRechazado) as e:
        return _volver(error=str(e))
    return _volver(guardado=aviso)


@router.post("/solicitudes/{solicitud_id}/rechazar")
async def rechazar(
    peticion: Request, actor: ActorWeb, sesion: SesionDep, solicitud_id: uuid.UUID
):
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    try:
        aviso = await rechazar_solicitud(
            sesion, solicitud_id, motivo=str(formulario.get("motivo", "")),
            quien=actor.usuario_id,
        )
    except (CierreNoExiste, CierreRechazado) as e:
        return _volver(error=str(e))
    return _volver(guardado=aviso)


def _uuid(valor) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valor)) if valor else None
    except ValueError:
        return None


def _volver(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/cierres"
    if guardado:
        destino += f"?guardado={quote(guardado)}"
    elif error:
        destino += f"?error={quote(error)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
