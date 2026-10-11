"""Objetivos de cualquier periodo, desde la app de la oficina (ADR 0002 §104).

«Quiero poder agregar los objetivos en la app; los objetivos pueden ser por el
periodo que yo decida, incluso por día.» La lógica vive en
`app/infra/objetivos.py`, la misma que usa el panel.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import ROLES_DE_OFICINA, Actor, ActorDep, SesionDep
from app.api.esquemas import Dinero
from app.infra.objetivos import (
    ObjetivoInvalido,
    borrar_objetivo,
    guardar_objetivo,
    objetivos_con_avance,
    rutas_para_objetivos,
)

router = APIRouter(prefix="/objetivos", tags=["objetivos"])

PERMISO_FIJAR = "objetivos.administrar"


def _exigir_ver(actor: Actor) -> None:
    actor.exigir("tablero.ver")
    if actor.rol not in ROLES_DE_OFICINA:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "falta el permiso de la oficina")


class Objetivo(BaseModel):
    id: str
    # 'mes' = el del mes por ruta (la barra del tablero); 'periodo' = cualquiera.
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


class PeticionObjetivo(BaseModel):
    # Nulo = toda la empresa.
    ruta_id: uuid.UUID | None = None
    desde: date
    hasta: date
    objetivo_venta: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    objetivo_visitas: int | None = Field(default=None, ge=0, le=100000)
    nota: str | None = Field(default=None, max_length=300)


@router.get("", response_model=Objetivos)
async def listar(actor: ActorDep, sesion: SesionDep) -> Objetivos:
    """Los que corren, los que vienen y los que terminaron hace poco, con su
    avance contra lo esperado a la fecha."""
    _exigir_ver(actor)
    return Objetivos(
        objetivos=[
            Objetivo(
                id=o.id, clase=o.clase, ruta_id=o.ruta_id, ruta=o.ruta, desde=o.desde,
                hasta=o.hasta, dias=o.dias, estado=o.estado,
                objetivo_venta=o.objetivo_venta, objetivo_visitas=o.objetivo_visitas,
                nota=o.nota, venta=o.venta, visitas=o.visitas, logrado=o.avance.logrado,
                esperado=o.avance.esperado, semaforo=o.avance.semaforo,
            )
            for o in await objetivos_con_avance(sesion)
        ],
        rutas=[Ruta(**r) for r in await rutas_para_objetivos(sesion)],
        puede_fijar=actor.puede(PERMISO_FIJAR),
        hoy=date.today(),
    )


@router.post("", response_model=Objetivos, status_code=status.HTTP_201_CREATED)
async def fijar(peticion: PeticionObjetivo, actor: ActorDep, sesion: SesionDep) -> Objetivos:
    _exigir_ver(actor)
    actor.exigir(PERMISO_FIJAR)
    try:
        await guardar_objetivo(
            sesion,
            ruta_id=peticion.ruta_id,
            desde=peticion.desde,
            hasta=peticion.hasta,
            objetivo_venta=peticion.objetivo_venta,
            objetivo_visitas=peticion.objetivo_visitas or None,
            nota=(peticion.nota or "").strip() or None,
            quien=actor.usuario_id,
        )
    except ObjetivoInvalido as e:
        await sesion.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    await sesion.commit()
    return await listar(actor, sesion)


@router.post("/{objetivo_id}/borrar", response_model=Objetivos)
async def borrar(objetivo_id: str, actor: ActorDep, sesion: SesionDep) -> Objetivos:
    _exigir_ver(actor)
    actor.exigir(PERMISO_FIJAR)
    if not await borrar_objetivo(sesion, objetivo_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ese objetivo ya no existe.")
    await sesion.commit()
    return await listar(actor, sesion)
