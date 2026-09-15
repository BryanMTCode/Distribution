"""Registro de dispositivos y asignación de rangos de folio."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import ActorDep, SesionDep
from app.infra.models import Dispositivo, FolioRango

router = APIRouter(prefix="/dispositivos", tags=["dispositivos"])

TAMANO_RANGO = 1000
TIPOS_DOCUMENTO = ("venta", "cobro", "merma", "no_drop")


class PeticionRegistro(BaseModel):
    # El UUID lo genera el dispositivo, igual que los documentos de campo.
    id: uuid.UUID
    etiqueta: str = Field(max_length=120, description="'Moto G54 — Bryan'")
    modelo: str | None = None
    so_version: str | None = None
    app_version: str | None = None
    impresora_mac: str | None = None
    impresora_ancho_mm: Literal[58, 80] | None = None


class DispositivoRegistrado(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    etiqueta: str
    estado: str
    ya_existia: bool


@router.post("/registrar", response_model=DispositivoRegistrado)
async def registrar(
    peticion: PeticionRegistro, actor: ActorDep, sesion: SesionDep
) -> DispositivoRegistrado:
    """Idempotente: reenviar el mismo registro no falla ni duplica.

    Es el mismo principio que el resto del sistema — la red puede entregar dos
    veces, y recibirlo dos veces no debe cambiar nada.
    """
    existente = await sesion.get(Dispositivo, peticion.id)
    if existente is not None:
        if existente.usuario_id != actor.usuario_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "ese dispositivo ya pertenece a otro usuario"
            )
        return DispositivoRegistrado(
            id=existente.id,
            usuario_id=existente.usuario_id,
            etiqueta=existente.etiqueta,
            estado=existente.estado,
            ya_existia=True,
        )

    dispositivo = Dispositivo(
        id=peticion.id,
        usuario_id=actor.usuario_id,
        etiqueta=peticion.etiqueta,
        modelo=peticion.modelo,
        so_version=peticion.so_version,
        app_version=peticion.app_version,
        impresora_mac=peticion.impresora_mac,
        impresora_ancho_mm=peticion.impresora_ancho_mm,
        estado="activo",
        ultimo_cursor_pull=0,
        registrado_en=datetime.now(UTC),
    )
    sesion.add(dispositivo)
    try:
        await sesion.commit()
    except IntegrityError as e:
        await sesion.rollback()
        # uq_dispositivo_activo_por_usuario: un usuario opera un solo equipo.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "el usuario ya tiene un dispositivo activo; revócalo antes de registrar otro",
        ) from e

    return DispositivoRegistrado(
        id=dispositivo.id,
        usuario_id=dispositivo.usuario_id,
        etiqueta=dispositivo.etiqueta,
        estado=dispositivo.estado,
        ya_existia=False,
    )


class RangoAsignado(BaseModel):
    documento_tipo: str
    desde: int
    hasta: int
    consumido_hasta: int


@router.post("/{dispositivo_id}/folios", response_model=list[RangoAsignado])
async def asignar_folios(
    dispositivo_id: uuid.UUID, actor: ActorDep, sesion: SesionDep
) -> list[RangoAsignado]:
    """Entrega un rango nuevo por tipo de documento.

    Si la app se reinstala, el contador local vuelve a 1. Sin rangos, el equipo
    empezaría a reimprimir folios que ya están en papel en manos de clientes.
    El constraint `no_traslape_rangos` (EXCLUDE) lo hace imposible.
    """
    dispositivo = await sesion.get(Dispositivo, dispositivo_id)
    if dispositivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dispositivo no encontrado")
    if dispositivo.usuario_id != actor.usuario_id and not actor.puede("dispositivos.administrar"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "el dispositivo es de otro usuario")

    asignados: list[RangoAsignado] = []
    for tipo in TIPOS_DOCUMENTO:
        tope = (
            await sesion.execute(
                text(
                    "SELECT COALESCE(MAX(hasta), 0) FROM folios_rangos "
                    "WHERE dispositivo_id = :dev AND documento_tipo = :tipo"
                ),
                {"dev": str(dispositivo_id), "tipo": tipo},
            )
        ).scalar_one()

        activo = (
            await sesion.execute(
                select(FolioRango).where(
                    FolioRango.dispositivo_id == dispositivo_id,
                    FolioRango.documento_tipo == tipo,
                    FolioRango.agotado.is_(False),
                )
            )
        ).scalars().first()

        if activo is not None:
            asignados.append(
                RangoAsignado(
                    documento_tipo=tipo,
                    desde=activo.desde,
                    hasta=activo.hasta,
                    consumido_hasta=activo.consumido_hasta,
                )
            )
            continue

        rango = FolioRango(
            dispositivo_id=dispositivo_id,
            documento_tipo=tipo,
            desde=tope + 1,
            hasta=tope + TAMANO_RANGO,
            consumido_hasta=tope,
            asignado_en=datetime.now(UTC),
            agotado=False,
        )
        sesion.add(rango)
        asignados.append(
            RangoAsignado(
                documento_tipo=tipo,
                desde=rango.desde,
                hasta=rango.hasta,
                consumido_hasta=rango.consumido_hasta,
            )
        )

    await sesion.commit()
    return asignados


class PeticionRevocar(BaseModel):
    motivo: str = Field(min_length=3, description="'equipo robado', 'baja del vendedor'")


@router.post("/{dispositivo_id}/revocar", status_code=status.HTTP_204_NO_CONTENT)
async def revocar(
    dispositivo_id: uuid.UUID, peticion: PeticionRevocar, actor: ActorDep, sesion: SesionDep
) -> None:
    """Mata el equipo: sus tokens dejan de servir en la siguiente petición."""
    actor.exigir("dispositivos.administrar")
    dispositivo = await sesion.get(Dispositivo, dispositivo_id)
    if dispositivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dispositivo no encontrado")

    dispositivo.estado = "revocado"
    dispositivo.revocado_en = datetime.now(UTC)
    dispositivo.revocado_motivo = peticion.motivo
    await sesion.execute(
        text(
            "UPDATE sesiones SET revocada_en = now() "
            "WHERE dispositivo_id = :dev AND revocada_en IS NULL"
        ),
        {"dev": str(dispositivo_id)},
    )
    await sesion.commit()
