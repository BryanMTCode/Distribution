"""Desempeño → Ganancia: lo vendido menos lo que costó (ADR 0002 §180).

«Quiero también la ganancia de lo que ya se vendió», por vendedor, por día y por
artículo, del periodo que se elija. Y, del mismo periodo, los gastos de la
empresa capturados en la tarjeta y la ganancia neta (§192). Solo en el dashboard
y solo para quien tiene `costos.ver`: el administrador. El cálculo está en
`app/infra/ganancia.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.admin.comun import SesionDep, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb
from app.infra.ganancia import ganancia_neta

router = APIRouter(prefix="/panel/ganancia", tags=["panel"], include_in_schema=False)

PERMISO = "costos.ver"


@router.get("", response_class=HTMLResponse)
async def pantalla(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    elegido = await periodo_pedido(sesion, periodo or "semana", desde, hasta or desde)
    return render(
        peticion,
        "ganancia.html",
        {
            "periodo": elegido,
            "periodos": PERIODOS,
            **await ganancia_neta(sesion, elegido.inicio, elegido.fin),
        },
        actor=actor,
        seccion="Desempeño",
    )
