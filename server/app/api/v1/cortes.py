"""El corte del día desde la app (`/v1/cortes`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
El corte se hace con el camión enfrente: quien cuenta está en el patio con el
teléfono en la mano, no frente a la computadora del panel. Pedido en operación
(octubre 2026), junto con las cargas desde la app.

────────────────────────────────────────────────────────────────────────────
LAS MISMAS REGLAS QUE EL PANEL, NO UNA COPIA DE ELLAS
────────────────────────────────────────────────────────────────────────────
Todo lo que decide algo se importa de `app/api/admin/liquidaciones.py`:
`abrir_corte`, `guardar_conteo`, `guardar_arqueo`, `cerrar_corte` y
`datos_del_corte`. El cierre desde el teléfono mueve el inventario, le carga a
la cuenta del vendedor lo que falta y le avisa a su teléfono EXACTAMENTE igual
que el del panel. Este archivo solo traduce de JSON a esas funciones.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
`inventario.liquidar`: admin, supervisor y —desde la migración 0045— gerente.
El vendedor no: cortarse su propio camión sería firmar su propio conteo.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.admin.comun import CapturaInvalida
from app.api.admin.liquidaciones import (
    PERMISO,
    CorteNoExiste,
    CorteRechazado,
    abrir_corte,
    cargas_por_cortar,
    cerrar_corte,
    cortes_recientes,
    datos_del_corte,
    guardar_arqueo,
    guardar_conteo,
)
from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero

router = APIRouter(prefix="/cortes", tags=["cortes"])


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class CargaPorCortar(BaseModel):
    carga_id: uuid.UUID
    folio: str
    fecha_operativa: date
    vendedor: str
    camion: str
    # Días desde la carga: una de hace tres días sin cortar es un camión que nadie
    # contó, y cada día que pasa el conteo vale menos.
    dias: int
    ventas: int
    importe: Dinero


class CorteEnLista(BaseModel):
    id: uuid.UUID
    folio: str
    estado: str
    fecha_operativa: date
    vendedor: str
    carga: str
    efectivo_esperado: Dinero
    efectivo_entregado: Dinero
    diferencia_efectivo: Dinero
    faltantes: int
    cerrada_en: datetime | None


class Cortes(BaseModel):
    por_cortar: list[CargaPorCortar]
    cortes: list[CorteEnLista]


class Presentacion(BaseModel):
    unidad: str
    factor: Cantidad


class RenglonDelCorte(BaseModel):
    id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    inicial: Cantidad
    cargada: Cantidad
    vendida: Cantidad
    merma: Cantidad
    devuelta: Cantidad
    esperado: Cantidad
    contada: Cantidad
    diferencia: Cantidad
    presentaciones: list[Presentacion]


class Respaldo(BaseModel):
    respaldado: bool
    motivo: str | None
    pendientes: int


class Cargo(BaseModel):
    origen: str
    importe: Dinero


class Corte(BaseModel):
    id: uuid.UUID
    folio: str
    estado: str
    abierto: bool
    fecha_operativa: date
    vendedor: str
    camion: str
    carga: str
    efectivo_esperado: Dinero
    efectivo_entregado: Dinero
    diferencia_efectivo: Dinero
    arqueo_hecho: bool
    observaciones: str | None
    renglones: list[RenglonDelCorte]
    # Lo que impide cerrar ahora mismo. Se ve mientras se cuenta.
    bloqueos: list[str]
    # Si hay un DATO de que el teléfono terminó de subir; si no, cerrar pide
    # confirmarlo a mano.
    respaldo: Respaldo
    cargos: list[Cargo]
    total_cargado: Dinero
    mensaje: str | None = None


class PeticionAbrir(BaseModel):
    carga_id: uuid.UUID


class PeticionConteo(BaseModel):
    # De id de renglón a lo que se contó, en unidad base. El renglón que no viene
    # vale CERO, como en el panel: al contar un camión, lo que no se anotó es lo
    # que no está arriba.
    contados: dict[str, str] = Field(max_length=2000)


class PeticionArqueo(BaseModel):
    efectivo: str = Field(max_length=20)
    observaciones: str = Field(default="", max_length=600)


class PeticionCerrar(BaseModel):
    confirmo_sincronizado: bool = False


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@router.get("", response_model=Cortes)
async def lista(actor: ActorDep, sesion: SesionDep) -> Cortes:
    actor.exigir(PERMISO)
    return Cortes(
        por_cortar=[
            CargaPorCortar(carga_id=c["id"], **{k: c[k] for k in (
                "folio", "fecha_operativa", "vendedor", "camion", "dias", "ventas",
                "importe")})
            for c in await cargas_por_cortar(sesion)
        ],
        cortes=[
            CorteEnLista(**{k: c[k] for k in CorteEnLista.model_fields})
            for c in await cortes_recientes(sesion)
        ],
    )


@router.post("", response_model=Corte)
async def abrir(peticion: PeticionAbrir, actor: ActorDep, sesion: SesionDep) -> Corte:
    """Abre el corte de una carga; si ya estaba abierto, devuelve ése."""
    actor.exigir(PERMISO)
    try:
        liquidacion_id = await abrir_corte(sesion, peticion.carga_id)
    except CorteNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except CorteRechazado as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _corte(sesion, liquidacion_id)


@router.get("/{liquidacion_id}", response_model=Corte)
async def ver(liquidacion_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Corte:
    actor.exigir(PERMISO)
    return await _corte(sesion, liquidacion_id)


@router.post("/{liquidacion_id}/conteo", response_model=Corte)
async def contar(
    liquidacion_id: uuid.UUID, peticion: PeticionConteo, actor: ActorDep, sesion: SesionDep
) -> Corte:
    actor.exigir(PERMISO)
    try:
        await guardar_conteo(sesion, liquidacion_id, peticion.contados)
    except CorteNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (CorteRechazado, CapturaInvalida) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _corte(sesion, liquidacion_id, mensaje="Conteo guardado.")


@router.post("/{liquidacion_id}/arqueo", response_model=Corte)
async def arqueo(
    liquidacion_id: uuid.UUID, peticion: PeticionArqueo, actor: ActorDep, sesion: SesionDep
) -> Corte:
    actor.exigir(PERMISO)
    try:
        aviso = await guardar_arqueo(
            sesion, liquidacion_id, peticion.efectivo, peticion.observaciones
        )
    except CorteNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (CorteRechazado, CapturaInvalida) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _corte(sesion, liquidacion_id, mensaje=aviso)


@router.post("/{liquidacion_id}/cerrar", response_model=Corte)
async def cerrar(
    liquidacion_id: uuid.UUID, peticion: PeticionCerrar, actor: ActorDep, sesion: SesionDep
) -> Corte:
    """Cierra el día. Bloqueado si el teléfono del vendedor no ha subido todo."""
    actor.exigir(PERMISO)
    try:
        aviso = await cerrar_corte(
            sesion,
            liquidacion_id,
            quien=actor.usuario_id,
            confirmo_sincronizado=peticion.confirmo_sincronizado,
        )
    except CorteNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except CorteRechazado as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _corte(sesion, liquidacion_id, mensaje=aviso)


async def _corte(sesion, liquidacion_id: uuid.UUID, *, mensaje: str | None = None) -> Corte:
    datos = await datos_del_corte(sesion, liquidacion_id)
    if datos is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese corte no existe")
    c = datos["cabecera"]
    return Corte(
        id=c["id"],
        folio=c["folio"],
        estado=c["estado"],
        abierto=c["estado"] != "cerrada",
        fecha_operativa=c["fecha_operativa"],
        vendedor=c["vendedor"],
        camion=c["camion"],
        carga=c["carga_folio"],
        efectivo_esperado=c["efectivo_esperado"],
        efectivo_entregado=c["efectivo_entregado"],
        diferencia_efectivo=datos["diferencia_efectivo"],
        arqueo_hecho=c.get("arqueo_en") is not None,
        observaciones=c.get("observaciones"),
        renglones=[
            RenglonDelCorte(
                id=r["id"],
                sku=r["sku"],
                nombre=r["nombre"],
                unidad_base=r["unidad_base"],
                inicial=r["cant_inicial"],
                cargada=r["cant_cargada"],
                vendida=r["cant_vendida"],
                merma=r["cant_merma"],
                devuelta=r["cant_devuelta"],
                esperado=r["esperado"],
                contada=r["cant_contada"],
                # La calculada con el módulo de dominio, la misma que el panel.
                diferencia=r["calculada"],
                presentaciones=[
                    Presentacion(unidad=p["unidad"], factor=Decimal(p["factor"]))
                    for p in (r["presentaciones"] or [])
                ],
            )
            for r in datos["renglones"]
        ],
        bloqueos=datos["bloqueos"],
        respaldo=Respaldo(**datos["respaldo"]),
        cargos=[Cargo(**dict(x)) for x in datos["cargos"]],
        total_cargado=datos["total_cargado"],
        mensaje=mensaje,
    )
