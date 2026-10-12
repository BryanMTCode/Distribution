"""Los objetivos en la app de la oficina: solo para ver (ADR 0002 §104, §115).

«Quiero que todo se sume.» El único objetivo que se captura es el diario de
cada vendedor, y se pone en el dashboard. La app ve lo que suma: hoy, esta
semana y este mes, de toda la empresa y de cada vendedor, con lo vendido y lo
esperado a la fecha. Lo calcula `infra/objetivos_diarios.metas_del_rango`, lo
mismo que la sección «Lo que suma» del dashboard.

Capturar desde la app ya no se puede: `puede_fijar` va siempre en falso —la app
esconde «Nuevo objetivo» y «Quitar»— y un teléfono con una app anterior que lo
intente recibe un 422 que dice dónde se pone.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import ROLES_DE_OFICINA, Actor, ActorDep, SesionDep
from app.api.esquemas import Dinero
from app.infra.objetivos import fin_de_mes, rutas_para_objetivos
from app.infra.objetivos_diarios import metas_del_rango

router = APIRouter(prefix="/objetivos", tags=["objetivos"])

SE_PONEN_EN_EL_DASHBOARD = (
    "Los objetivos ya no se ponen desde la app: en el dashboard, Objetivos → «Objetivo "
    "diario de cada vendedor». La semana, el mes y la empresa se suman solos."
)


def _exigir_ver(actor: Actor) -> None:
    actor.exigir("tablero.ver")
    if actor.rol not in ROLES_DE_OFICINA:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "falta el permiso de la oficina")


class Objetivo(BaseModel):
    id: str
    # 'suma': la suma de los objetivos diarios del periodo (§115).
    clase: str
    ruta_id: uuid.UUID | None
    # Nulo = toda la empresa.
    ruta: str | None
    desde: date
    hasta: date
    dias: int
    estado: str
    objetivo_venta: Dinero
    objetivo_visitas: int | None
    nota: str | None
    venta: Dinero
    visitas: int
    logrado: Decimal | None
    esperado: Decimal
    semaforo: str


class Ruta(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str


class Objetivos(BaseModel):
    objetivos: list[Objetivo]
    rutas: list[Ruta]
    puede_fijar: bool
    hoy: date


def _periodos(hoy: date) -> list[tuple[str, str, date, date]]:
    """Hoy, la semana (de lunes a domingo) y el mes completos del día dado."""
    lunes = hoy - timedelta(days=hoy.weekday())
    return [
        ("hoy", "Hoy", hoy, hoy),
        ("semana", "Esta semana", lunes, lunes + timedelta(days=6)),
        ("mes", "Este mes", hoy.replace(day=1), fin_de_mes(hoy)),
    ]


@router.get("", response_model=Objetivos)
async def listar(actor: ActorDep, sesion: SesionDep) -> Objetivos:
    """Hoy, esta semana y este mes: primero toda la empresa, luego cada
    vendedor que tenga objetivo en el periodo."""
    _exigir_ver(actor)
    hoy = date.today()
    por_dueno: dict[uuid.UUID | None, list[Objetivo]] = {}
    for clave, etiqueta, desde, hasta in _periodos(hoy):
        metas, empresa, _ = await metas_del_rango(sesion, desde, hasta)
        for m in [empresa, *metas]:
            if not m.tiene_objetivo:
                continue
            de_quien = "todos los vendedores" if m.ruta_id is None else "sus objetivos diarios"
            por_dueno.setdefault(m.ruta_id, []).append(
                Objetivo(
                    id=f"suma-{m.ruta_id or 'empresa'}-{clave}",
                    clase="suma",
                    ruta_id=m.ruta_id,
                    ruta=m.ruta,
                    desde=desde,
                    hasta=hasta,
                    dias=(hasta - desde).days + 1,
                    estado="en_curso",
                    objetivo_venta=m.objetivo,
                    objetivo_visitas=None,
                    nota=f"{etiqueta}: la suma de {de_quien}.",
                    venta=m.venta,
                    visitas=m.visitas,
                    logrado=m.logrado,
                    esperado=m.esperado,
                    semaforo=m.semaforo,
                )
            )
    return Objetivos(
        objetivos=[o for lista in por_dueno.values() for o in lista],
        rutas=[Ruta(**r) for r in await rutas_para_objetivos(sesion)],
        puede_fijar=False,
        hoy=hoy,
    )


class RenglonDeAvance(BaseModel):
    ruta_id: uuid.UUID | None
    # Nulo = toda la empresa (la suma).
    ruta: str | None
    # Nulo: sin objetivo diario en el periodo (no se pinta barra).
    objetivo: Dinero | None
    venta: Dinero
    logrado: Decimal | None
    esperado: Decimal | None
    semaforo: str
    falta: Dinero


class AvancePorVista(BaseModel):
    vista: str
    desde: date
    hasta: date
    empresa: RenglonDeAvance
    rutas: list[RenglonDeAvance]
    # Aparte: no es de ningún vendedor, así que no entra en la suma.
    mayoreo: Dinero


VISTAS = ("hoy", "semana", "mes")


def _renglon(m) -> RenglonDeAvance:
    con = m.tiene_objetivo
    return RenglonDeAvance(
        ruta_id=m.ruta_id,
        ruta=m.ruta,
        objetivo=m.objetivo if con else None,
        venta=m.venta,
        logrado=m.logrado,
        esperado=m.esperado if con else None,
        semaforo=m.semaforo,
        falta=m.falta,
    )


@router.get("/avance", response_model=AvancePorVista)
async def avance(
    actor: ActorDep, sesion: SesionDep, vista: str = "mes", fecha: date | None = None
) -> AvancePorVista:
    """La barra del Tablero de la app por la vista que se escoja (ADR 0002 §116):
    el día, la semana o el mes del día que se está viendo. Cada vendedor contra
    la suma de sus objetivos diarios del periodo, y la empresa como la suma."""
    _exigir_ver(actor)
    if vista not in VISTAS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "vista: hoy, semana o mes")
    dia = fecha or date.today()
    _, _, desde, hasta = next(p for p in _periodos(dia) if p[0] == vista)
    metas, empresa, mayoreo = await metas_del_rango(sesion, desde, hasta)
    return AvancePorVista(
        vista=vista,
        desde=desde,
        hasta=hasta,
        empresa=_renglon(empresa),
        rutas=[_renglon(m) for m in metas],
        mayoreo=mayoreo,
    )


@router.post("", status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
async def fijar(actor: ActorDep) -> None:
    """Una app anterior que intenta capturar: se le dice dónde se pone."""
    _exigir_ver(actor)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, SE_PONEN_EN_EL_DASHBOARD)


@router.post("/{objetivo_id}/borrar", status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
async def borrar(objetivo_id: str, actor: ActorDep) -> None:  # noqa: ARG001
    _exigir_ver(actor)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, SE_PONEN_EN_EL_DASHBOARD)
