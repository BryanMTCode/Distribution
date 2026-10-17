"""Reporte en Excel para el administrador (ADR 0002 §127).

Una pantalla con el selector de periodo de siempre y un botón: «Bajar el
reporte en Excel». Lo que lleva cada hoja y de dónde sale cada cifra está en
`app/infra/reporte_excel.py`.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.periodo import PERIODOS, periodo_pedido
from app.api.admin.sesion_web import ActorWeb
from app.infra import reporte_excel

router = APIRouter(prefix="/panel/reporte", tags=["panel"], include_in_schema=False)

PERMISO = "tablero.ver"

# La hora que se imprime en el reporte es la de quien vende (ver `app/core/db.py`).
ZONA = ZoneInfo("America/Mazatlan")

TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
        "reporte.html",
        {"periodo": elegido, "periodos": PERIODOS},
        actor=actor,
        seccion="Reporte en Excel",
    )


@router.get("/excel")
async def bajar(
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
) -> Response:
    actor.exigir(PERMISO)
    elegido = await periodo_pedido(sesion, periodo or "semana", desde, hasta or desde)
    datos = await reporte_excel.datos_del_reporte(sesion, elegido)
    quien = (
        await sesion.execute(
            text("SELECT nombre FROM usuarios WHERE id = :u"), {"u": actor.usuario_id}
        )
    ).scalar_one_or_none()
    archivo = reporte_excel.armar_libro(
        datos, generado_por=quien or "la oficina", generado_en=datetime.now(ZONA)
    )
    return Response(
        archivo,
        media_type=TIPO_XLSX,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{reporte_excel.nombre_del_archivo(datos)}"'
            )
        },
    )
