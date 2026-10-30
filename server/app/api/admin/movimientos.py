"""Movimientos: de cuánto estaba a cuánto pasó cada artículo (ADR 0002 §189).

«En los ajustes y eso quiero ver de cuántos estaba a cuánto pasó: entradas,
salidas, ajustes, todo eso.» Y antes (§188): sin tener que entrar a cada folio.

Una pestaña de Inventario con todo lo que movió la mercancía en el periodo, un
renglón por artículo y almacén: qué fue, el folio, lo que había, el cambio y lo
que quedó. Se filtra por qué fue y por almacén. Las ventas de los vendedores no
salen si no se piden: son la mayoría de los renglones. El cálculo está en
`app/infra/antes_y_despues.py`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb
from app.infra.antes_y_despues import GRUPOS, LIMITE, NOMBRE_DEL_GRUPO, del_periodo

router = APIRouter(
    prefix="/panel/inventario/movimientos", tags=["panel"], include_in_schema=False
)


@router.get("", response_class=HTMLResponse)
async def pantalla(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    que: str = "",
    almacen: str = "",
) -> HTMLResponse:
    actor.exigir("inventario.ver")
    elegido = await periodo_pedido(sesion, periodo or "hoy", desde, hasta or desde)
    que = que if que in NOMBRE_DEL_GRUPO else ""
    try:
        almacen_id = uuid.UUID(almacen) if almacen else None
    except ValueError:
        almacen_id = None

    filas = await del_periodo(
        sesion, desde=elegido.inicio, hasta=elegido.fin, grupo=que, almacen=almacen_id
    )
    return render(
        peticion,
        "movimientos.html",
        {
            "filas": filas,
            "periodo": elegido,
            "periodos": PERIODOS,
            "grupos": GRUPOS,
            "que": que,
            "almacen": str(almacen_id) if almacen_id else "",
            "almacenes": (
                await sesion.execute(
                    text(
                        "SELECT id, nombre, tipo FROM almacenes "
                        " WHERE tipo IN ('bodega', 'camion') AND activo "
                        " ORDER BY tipo, nombre"
                    )
                )
            ).mappings().all(),
            "limite": LIMITE,
        },
        actor=actor,
        seccion="Inventario",
    )
