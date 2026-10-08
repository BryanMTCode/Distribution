"""Los cierres de los vendedores, en la app del gerente (`/v1/cierres`).

El vendedor hace su corte y pide la carga de mañana desde su teléfono; aquí el
gerente los resuelve POR SEPARADO (ADR 0002 §82):

  · `/cortes`: los cortes por cerrar. Cerrar uno liquida el día del vendedor;
    el camión no se cuenta, se queda con lo que calcula el sistema.
  · `/solicitudes`: las cargas pedidas. Aceptar una crea y confirma la carga de
    mañana; espera a que el corte de hoy esté cerrado.

Todo lo que decide algo está en `app/infra/cierre_del_vendedor.py`, el mismo
que usa el panel: este archivo solo traduce de JSON a esas funciones.

`GET /v1/cierres` y `POST /v1/cierres/aceptar` —los dos juntos— son de la app
anterior a la versión +28 y se quedan para que siga funcionando.

Quién: los cortes exigen `inventario.liquidar`; las cargas, `inventario.cargar`.
El gerente tiene los dos (migraciones 0044 y 0045); el vendedor ninguno.
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
    aceptar_solicitud,
    cerrar_corte_del_vendedor,
    cierres,
    lista_de_cortes,
    lista_de_solicitudes,
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
    """Lo que le queda al camión, calculado: nadie lo cuenta."""

    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    traia: Cantidad
    cargada: Cantidad
    vendida: Cantidad
    merma: Cantidad
    devuelta: Cantidad
    queda: Cantidad
    # Para la app anterior a la versión +28, que esperaba un conteo.
    contada: Cantidad
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


class CortePorCerrar(BaseModel):
    id: uuid.UUID
    fecha_operativa: date


class Cierre(BaseModel):
    vendedor_id: uuid.UUID
    vendedor_codigo: str
    vendedor: str
    camion: str | None
    corte: CorteDelVendedor | None
    solicitud: SolicitudDeCarga | None
    # El corte que el vendedor todavía debe: mientras esté abierto, la carga
    # pedida no se puede aceptar.
    corte_por_cerrar: CortePorCerrar | None = None
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


class PeticionAceptarSolicitud(BaseModel):
    # De id de producto a cuántos bultos (de la presentación que pidió); «0» lo
    # quita. Lo que no viene se acepta como se pidió.
    bultos: dict[str, str] = Field(default_factory=dict, max_length=500)


class PeticionRechazar(BaseModel):
    motivo: str = Field(max_length=300)


def _error(e: Exception) -> HTTPException:
    if isinstance(e, CierreNoExiste):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return HTTPException(status.HTTP_409_CONFLICT, str(e))


# ---------------------------------------------------------------------------
# El corte
# ---------------------------------------------------------------------------
@router.get("/cortes", response_model=Cierres)
async def cortes(actor: ActorDep, sesion: SesionDep) -> Cierres:
    actor.exigir(PERMISO_CERRAR)
    datos = await lista_de_cortes(sesion)
    return Cierres(
        pendientes=[Cierre(**c) for c in datos["pendientes"]],
        recientes=[Cierre(**c) for c in datos["recientes"]],
    )


@router.post("/cortes/{corte_id}/cerrar", response_model=Cierre)
async def cerrar_corte(corte_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Cierre:
    actor.exigir(PERMISO_CERRAR)
    try:
        aviso = await cerrar_corte_del_vendedor(sesion, corte_id, quien=actor.usuario_id)
    except (CierreNoExiste, CierreRechazado) as e:
        raise _error(e) from e
    return Cierre(**await un_cierre(sesion, corte_id=corte_id), mensaje=aviso)


# ---------------------------------------------------------------------------
# La carga pedida
# ---------------------------------------------------------------------------
@router.get("/solicitudes", response_model=Cierres)
async def solicitudes(actor: ActorDep, sesion: SesionDep) -> Cierres:
    actor.exigir(PERMISO_VER)
    datos = await lista_de_solicitudes(sesion)
    return Cierres(
        pendientes=[Cierre(**c) for c in datos["pendientes"]],
        recientes=[Cierre(**c) for c in datos["recientes"]],
    )


@router.post("/solicitudes/{solicitud_id}/aceptar", response_model=Cierre)
async def aceptar_carga(
    solicitud_id: uuid.UUID,
    peticion: PeticionAceptarSolicitud,
    actor: ActorDep,
    sesion: SesionDep,
) -> Cierre:
    """Crea y confirma la carga de mañana. Se puede reintentar."""
    actor.exigir(PERMISO_VER)
    try:
        aviso = await aceptar_solicitud(
            sesion, solicitud_id, quien=actor.usuario_id, bultos=peticion.bultos
        )
    except (CierreNoExiste, CierreRechazado) as e:
        raise _error(e) from e
    return Cierre(
        **await un_cierre(sesion, solicitud_id=solicitud_id, solo=True),
        mensaje=aviso,
    )


# ---------------------------------------------------------------------------
# Los dos juntos: la app anterior a la versión +28
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
    except (CierreNoExiste, CierreRechazado) as e:
        raise _error(e) from e
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
    except (CierreNoExiste, CierreRechazado) as e:
        raise _error(e) from e
    return Cierre(**await un_cierre(sesion, solicitud_id=solicitud_id, solo=True), mensaje=aviso)
