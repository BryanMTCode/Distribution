"""Cortes y cargas por aceptar: lo que mandan los vendedores, en el panel.

El vendedor hace su corte y pide la carga de mañana desde el teléfono. Aquí la
oficina los resuelve POR SEPARADO (ADR 0002 §82): primero cierra el corte —el
camión no se cuenta, se queda con lo que calcula el sistema; si no entregó el
efectivo, lo que falte va a su cuenta— y después acepta la carga, que se crea y
se confirma desde la bodega principal. Todo lo que decide algo está en
`app/infra/cierre_del_vendedor.py`, el mismo que usa la app del gerente
(`/v1/cierres`).

Ver exige `inventario.cargar`; cerrar el corte exige además
`inventario.liquidar`.
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
    aceptar_solicitud,
    cerrar_corte_del_vendedor,
    lista_de_cortes,
    lista_de_solicitudes,
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
    cortes = await lista_de_cortes(sesion)
    solicitudes = await lista_de_solicitudes(sesion)
    for c in cortes["pendientes"] + cortes["recientes"]:
        c["corte"]["resumen"] = resumen_de_efectivo(c["corte"])
    return render(
        peticion,
        "cierres.html",
        {
            "cortes": cortes["pendientes"],
            "cortes_recientes": cortes["recientes"],
            "solicitudes": solicitudes["pendientes"],
            "solicitudes_recientes": solicitudes["recientes"],
            "puede_cerrar": actor.puede(PERMISO_CERRAR),
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Cortes y cargas por aceptar",
    )


@router.post("/cortes/{corte_id}/cerrar")
async def cerrar_corte(peticion: Request, actor: ActorWeb, sesion: SesionDep, corte_id: uuid.UUID):
    actor.exigir(PERMISO)
    actor.exigir(PERMISO_CERRAR)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    try:
        aviso = await cerrar_corte_del_vendedor(sesion, corte_id, quien=actor.usuario_id)
    except (CierreNoExiste, CierreRechazado) as e:
        return _volver(error=str(e))
    return _volver(guardado=aviso)


@router.post("/solicitudes/{solicitud_id}/aceptar")
async def aceptar(
    peticion: Request, actor: ActorWeb, sesion: SesionDep, solicitud_id: uuid.UUID
):
    actor.exigir(PERMISO)
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    bultos = {
        str(clave)[len("bultos_"):]: str(valor)
        for clave, valor in formulario.items()
        if str(clave).startswith("bultos_") and str(valor).strip() != ""
    }
    try:
        aviso = await aceptar_solicitud(
            sesion, solicitud_id, quien=actor.usuario_id, bultos=bultos
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


def _volver(*, guardado: str = "", error: str = "") -> RedirectResponse:
    destino = "/panel/cierres"
    if guardado:
        destino += f"?guardado={quote(guardado)}"
    elif error:
        destino += f"?error={quote(error)}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
