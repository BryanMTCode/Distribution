"""Objetivos: el diario de cada vendedor, y lo que suma (ADR 0002 §110, §115).

«Quiero que todo se sume.» Se captura UNA sola cosa: el objetivo diario de cada
vendedor —por ejemplo $3,500 de lunes a sábado—, que vale hasta que se cambie.
Lo demás no se captura, se calcula:

  · la semana, el mes o las fechas que se escojan son la suma de sus días;
  · toda la empresa es la suma de todos los vendedores.

Antes había además un objetivo del mes por ruta y objetivos de periodo, cada
uno capturado aparte y sin sumarse con nada; tres cifras que podían decir cosas
distintas de la misma ruta. Se quitaron (§115).

El objetivo del mes de cada ruta —la barra del Tablero de la app— ahora es la
suma de sus objetivos diarios del mes, y lo esperado a la fecha es lo que suman
los días ya transcurridos: un domingo de descanso no atrasa a nadie, que era el
temor original de un objetivo diario.

Sin JavaScript, como todo el panel.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette import status

from app.api.admin.comun import (
    CapturaInvalida,
    SesionDep,
    leer_dinero,
    leer_entero,
    render,
    texto_o_nulo,
)
from app.api.admin.periodo import PERIODOS, Periodo, periodo_pedido
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra.objetivos import ObjetivoInvalido, fin_de_mes, rutas_para_objetivos
from app.infra.objetivos_diarios import (
    DIAS_CORTOS,
    LUNES_A_SABADO,
    cancelar_cambio,
    de_toda_la_empresa,
    fijar_objetivo_diario,
    metas_del_rango,
    objetivos_diarios,
    quitar_objetivo_diario,
)

router = APIRouter(prefix="/panel/objetivos", tags=["panel"], include_in_schema=False)

PERMISO = "objetivos.administrar"

# «Todos los vendedores» en «De quién»: la MISMA meta a cada ruta activa, una
# por ruta (§113).
TODAS = "todas"


def completo(periodo: Periodo) -> Periodo:
    """«Esta semana» y «Este mes» completos, no a la fecha: el objetivo de la
    semana es de lunes a domingo, y el del mes, del 1 al último. Lo que va a la
    fecha es lo «esperado»."""
    if periodo.clave == "semana":
        return dataclasses.replace(periodo, fin=periodo.inicio + timedelta(days=6))
    if periodo.clave == "mes":
        return dataclasses.replace(periodo, fin=fin_de_mes(periodo.inicio))
    return periodo


async def _rutas_activas(sesion) -> list[dict]:
    rutas = await rutas_para_objetivos(sesion)
    if not rutas:
        raise ObjetivoInvalido("No hay rutas activas a quién ponerle la meta.")
    return rutas


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    aviso: str = "",
    error: str = "",
) -> HTMLResponse:
    """El objetivo diario de cada vendedor con su última semana, y lo que suma
    en el periodo que se escoja (el mes, por omisión)."""
    actor.exigir("tablero.ver")
    diarios = await objetivos_diarios(sesion)
    elegido = completo(await periodo_pedido(sesion, periodo or "mes", desde, hasta or desde))
    metas, empresa, mayoreo = await metas_del_rango(sesion, elegido.inicio, elegido.fin)
    return render(
        peticion,
        "objetivos.html",
        {
            "diarios": diarios,
            "empresa_dias": de_toda_la_empresa(diarios),
            "metas": metas,
            "empresa": empresa,
            "mayoreo": mayoreo,
            "periodo": elegido,
            "periodos": PERIODOS,
            "puede_editar": actor.puede(PERMISO),
            "dias_cortos": DIAS_CORTOS,
            "lunes_a_sabado": LUNES_A_SABADO,
            "hoy": date.today().isoformat(),
            "aviso": aviso,
            "error": error,
        },
        actor=actor,
        seccion="Desempeño",
    )


# ---------------------------------------------------------------------------
# El objetivo diario fijo (ADR 0002 §110)
# ---------------------------------------------------------------------------


def _al_diario(**que: str) -> RedirectResponse:
    clave, texto = next(iter(que.items()))
    return RedirectResponse(
        f"/panel/objetivos?{clave}={quote(texto)}#diario", status_code=status.HTTP_303_SEE_OTHER
    )


def _dueno(ruta_id: str) -> uuid.UUID | None:
    """El vendedor (su ruta). En blanco no es nadie: la empresa no tiene
    objetivo propio, es la suma (§115), y `fijar_objetivo_diario` lo dice."""
    return uuid.UUID(ruta_id) if ruta_id else None


@router.post("/diario")
async def fijar_diario(peticion: Request, actor: ActorWeb, sesion: SesionDep) -> RedirectResponse:
    """«Por día 3,500, hasta que yo lo modifique»: pone o cambia el objetivo
    diario de un vendedor, o el mismo a todos los vendedores (§113), desde la
    fecha que se diga."""
    formulario = await peticion.form()
    exigir_csrf(peticion, str(formulario.get("csrf") or ""))
    actor.exigir(PERMISO)
    a_todos = str(formulario.get("ruta_id") or "") == TODAS
    try:
        if a_todos:
            duenos = [r["id"] for r in await _rutas_activas(sesion)]
        else:
            duenos = [_dueno(str(formulario.get("ruta_id") or ""))]
        texto_desde = str(formulario.get("desde") or "")
        desde = date.fromisoformat(texto_desde) if texto_desde else date.today()
        monto = leer_dinero(str(formulario.get("objetivo_venta") or ""),
                            campo="El objetivo de cada día")
        visitas = leer_entero(str(formulario.get("objetivo_visitas") or ""),
                              campo="Las visitas de cada día", maximo=10000)
        for dueno in duenos:
            tramo = await fijar_objetivo_diario(
                sesion,
                ruta_id=dueno,
                desde=desde,
                objetivo_venta=monto,
                objetivo_visitas=visitas or None,
                dias_semana=formulario.getlist("dias"),
                nota=texto_o_nulo(str(formulario.get("nota") or ""), maximo=300),
                quien=actor.usuario_id,
            )
    except (CapturaInvalida, ObjetivoInvalido, ValueError) as e:
        await sesion.rollback()
        return _al_diario(error=str(e))
    await sesion.commit()
    cuando = "desde hoy" if desde == date.today() else f"desde el {desde:%d/%m/%Y}"
    para_quien = f" para los {len(duenos)} vendedores" if a_todos else ""
    return _al_diario(
        aviso=f"Objetivo diario guardado{para_quien}: ${monto:,.2f} cada día, "
              f"{tramo.dias_en_palabras}, {cuando}. Vale hasta que lo cambies."
    )


@router.post("/diario/quitar")
async def quitar_diario(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    csrf: str = Form(""),
    ruta_id: str = Form(""),
) -> RedirectResponse:
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    try:
        await quitar_objetivo_diario(sesion, ruta_id=_dueno(ruta_id), quien=actor.usuario_id)
    except (ObjetivoInvalido, ValueError) as e:
        await sesion.rollback()
        return _al_diario(error=str(e))
    await sesion.commit()
    return _al_diario(
        aviso="Objetivo diario quitado desde hoy. Los días pasados se quedan como estaban."
    )


@router.post("/diario/{tramo_id}/cancelar")
async def cancelar_cambio_diario(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    tramo_id: uuid.UUID,
    csrf: str = Form(""),
) -> RedirectResponse:
    """Cancela un cambio programado que todavía no empieza."""
    exigir_csrf(peticion, csrf)
    actor.exigir(PERMISO)
    cancelado = await cancelar_cambio(sesion, tramo_id)
    await sesion.commit()
    if not cancelado:
        return _al_diario(error="Ese cambio ya empezó o ya no existe.")
    return _al_diario(aviso="Cambio programado cancelado.")
