"""Los cierres de los vendedores, en la app del gerente (`/v1/cierres`).

El vendedor hace su corte y pide la carga de mañana desde su teléfono; aquí el
gerente los revisa juntos y los acepta: el corte se cierra y la carga se crea y
se confirma (ADR 0002 §82). Todo lo que decide algo está en
`app/infra/cierre_del_vendedor.py`, el mismo que usa el panel: este archivo
solo traduce de JSON a esas funciones.

Quién: ver exige `inventario.cargar`; aceptar exige además
`inventario.liquidar`, porque cierra un corte. El gerente tiene los dos
(migraciones 0044 y 0045); el vendedor ninguno.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero
from app.infra.cierre_del_vendedor import (
    CierreNoExiste,
    CierreRechazado,
    aceptar,
    cierres,
    rechazar_solicitud,
    un_cierre,
)

router = APIRouter(prefix="/cierres", tags=["cierres"])

PERMISO_VER = "inventario.cargar"
PERMISO_CERRAR = "inventario.liquidar"


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class RenglonDelCorte(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    contada: Cantidad
    # Falso si el vendedor no lo contó: al cerrar vale cero.
    contado: bool
    sistema: Cantidad
    diferencia: Cantidad


class CorteDelVendedor(BaseModel):
    id: uuid.UUID
    fecha_operativa: date
    estado: str
    efectivo_declarado: Dinero
    efectivo_esperado: Dinero
    diferencia_efectivo: Dinero
    observaciones: str | None
    recibido_en: datetime
    resuelto_en: datetime | None
    liquidacion_folio: str | None
    nota: str | None
    renglones: list[RenglonDelCorte]


class RenglonDeLaSolicitud(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    unidad_codigo: str
    factor: Cantidad
    bultos: Cantidad
    cantidad: Cantidad
    cantidad_aceptada: Cantidad | None
    en_bodega: Cantidad


class SolicitudDeCarga(BaseModel):
    id: uuid.UUID
    fecha_operativa: date
    estado: str
    observaciones: str | None
    recibido_en: datetime
    resuelta_en: datetime | None
    resuelta_por: str | None
    motivo: str | None
    carga_folio: str | None
    bodega: str | None
    renglones: list[RenglonDeLaSolicitud]


class Cierre(BaseModel):
    vendedor_id: uuid.UUID
    vendedor_codigo: str
    vendedor: str
    camion: str | None
    corte: CorteDelVendedor | None
    solicitud: SolicitudDeCarga | None
    mensaje: str | None = None


class Cierres(BaseModel):
    pendientes: list[Cierre]
    recientes: list[Cierre]


class PeticionAceptar(BaseModel):
    corte_id: uuid.UUID | None = None
    solicitud_id: uuid.UUID | None = None
    # De id de producto a cuántos bultos (de la presentación que pidió); «0» lo
    # quita. Lo que no viene se acepta como se pidió.
    bultos: dict[str, str] = Field(default_factory=dict, max_length=500)


class PeticionRechazar(BaseModel):
    motivo: str = Field(max_length=300)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@router.get("", response_model=Cierres)
async def lista(actor: ActorDep, sesion: SesionDep) -> Cierres:
    actor.exigir(PERMISO_VER)
    datos = await cierres(sesion)
    return Cierres(
        pendientes=[Cierre(**c) for c in datos["pendientes"]],
        recientes=[Cierre(**c) for c in datos["recientes"]],
    )


@router.post("/aceptar", response_model=Cierre)
async def aceptar_cierre(
    peticion: PeticionAceptar, actor: ActorDep, sesion: SesionDep
) -> Cierre:
    """Cierra el corte y crea la carga de mañana. Se puede reintentar."""
    actor.exigir(PERMISO_VER)
    actor.exigir(PERMISO_CERRAR)
    try:
        aviso = await aceptar(
            sesion,
            corte_id=peticion.corte_id,
            solicitud_id=peticion.solicitud_id,
            quien=actor.usuario_id,
            bultos=peticion.bultos,
        )
    except CierreNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except CierreRechazado as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    cierre = await un_cierre(
        sesion, corte_id=peticion.corte_id, solicitud_id=peticion.solicitud_id
    )
    return Cierre(**cierre, mensaje=aviso)


@router.post("/solicitudes/{solicitud_id}/rechazar", response_model=Cierre)
async def rechazar(
    solicitud_id: uuid.UUID, peticion: PeticionRechazar, actor: ActorDep, sesion: SesionDep
) -> Cierre:
    actor.exigir(PERMISO_VER)
    try:
        aviso = await rechazar_solicitud(
            sesion, solicitud_id, motivo=peticion.motivo, quien=actor.usuario_id
        )
    except CierreNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except CierreRechazado as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return Cierre(**await un_cierre(sesion, solicitud_id=solicitud_id), mensaje=aviso)
