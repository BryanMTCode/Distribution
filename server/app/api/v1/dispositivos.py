"""Registro de dispositivos y asignación de rangos de folio."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api.deps import ActorDep, ActorQueEntregaDep, SesionDep
from app.core.config import obtener_config
from app.infra.models import Dispositivo, FolioRango, Usuario

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


# ---------------------------------------------------------------------------
# Borrado remoto (Fase 9)
# ---------------------------------------------------------------------------
# La regla que gobierna esto: NUNCA SE BORRA LO QUE NO SE HA ENTREGADO.
#
# El motivo real de un borrado casi nunca es un robo —es una renuncia, un cambio
# de teléfono, un equipo extraviado— y en los tres casos el aparato puede traer
# dentro un día de ventas sin sincronizar. Borrarlas es perder dinero cobrado
# sin saber a quién se le vendió.
#
# Y cuando sí es un robo, borrar rápido no gana nada: la base local está cifrada
# con SQLCipher y su llave vive en el Keystore detrás del PIN.
#
# Así que: ORDENAR → DRENAR → BORRAR → CONFIRMAR. Ver migración 0023.


class OrdenesDelServidor(BaseModel):
    """Lo que el servidor le tiene que decir al equipo en cada sincronización.

    Es el único canal servidor → dispositivo que no son datos, y existe porque
    el teléfono no puede enterarse de otra forma: un equipo al que se le ordenó
    el borrado y que no tiene nada en la cola nunca haría push, y por lo tanto
    nunca recibiría la orden.
    """

    estado: str

    # Si hay orden de borrado pendiente. El teléfono entrega su cola primero.
    borrar: bool
    borrado_motivo: str | None

    # Días que el equipo puede seguir operando sin sincronizar antes de que su
    # credencial local caduque y el login offline deje de funcionar. Se manda
    # para poder AVISAR ANTES: que el vendedor se enterara a las 6 de la mañana,
    # en la bodega, con el camión cargado, es un día perdido evitable.
    dias_max_offline: int
    dias_sin_sincronizar: int | None


def _dias_sin_sincronizar(dispositivo: Dispositivo) -> int | None:
    if dispositivo.ultima_sync_push_en is None:
        return None
    ultima = dispositivo.ultima_sync_push_en
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - ultima).days)


@router.get("/mio", response_model=OrdenesDelServidor)
async def mis_ordenes(actor: ActorQueEntregaDep, sesion: SesionDep) -> OrdenesDelServidor:
    """Lo que el equipo pregunta al empezar cada sincronización.

    Usa `ActorQueEntregaDep` —acepta un equipo `suspendido`— porque si lo
    rechazara, el equipo al que se le ordenó el borrado no podría leer la orden
    que lo manda a borrarse. Sería la única orden del sistema imposible de
    entregar a su destinatario.
    """
    if actor.dispositivo_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "esta consulta exige un token emitido para un dispositivo registrado",
        )
    dispositivo = await sesion.get(Dispositivo, actor.dispositivo_id)
    if dispositivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dispositivo no encontrado")

    usuario = await sesion.get(Usuario, dispositivo.usuario_id)
    cfg = obtener_config()
    return OrdenesDelServidor(
        estado=dispositivo.estado,
        borrar=(
            dispositivo.borrado_ordenado_en is not None
            and dispositivo.borrado_confirmado_en is None
        ),
        borrado_motivo=dispositivo.borrado_motivo,
        dias_max_offline=(
            (usuario.dias_max_offline if usuario else None) or cfg.dias_max_offline
        ),
        dias_sin_sincronizar=_dias_sin_sincronizar(dispositivo),
    )


class ConfirmacionDeBorrado(BaseModel):
    # Cuántos sobres le quedaban al borrar. Tiene que ser 0: el teléfono no
    # borra con cola pendiente. Se recibe y se guarda para poder DEMOSTRARLO
    # después — si algún día llega un número distinto, es que una versión del
    # cliente se saltó la regla, y esta columna es la única forma de notarlo.
    cola_pendiente: int = Field(ge=0, strict=True)


@router.post("/mio/borrado", status_code=status.HTTP_204_NO_CONTENT)
async def confirmar_borrado(
    confirmacion: ConfirmacionDeBorrado,
    actor: ActorQueEntregaDep,
    sesion: SesionDep,
) -> None:
    """El equipo confirma que ya borró su base y su credencial.

    Es lo único que prueba que el borrado OCURRIÓ: la orden sola solo prueba que
    alguien la pidió. A partir de aquí el equipo queda `revocado` y sus sesiones
    muertas, así que esta petición es la ÚLTIMA que puede hacer — por eso
    confirma después de borrar y no antes: si se cortara la red justo aquí, el
    teléfono ya está limpio y la oficina lo verá como «orden sin confirmar», que
    es el lado correcto del que equivocarse.
    """
    if actor.dispositivo_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token sin dispositivo")

    dispositivo = await sesion.get(Dispositivo, actor.dispositivo_id)
    if dispositivo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dispositivo no encontrado")
    if dispositivo.borrado_ordenado_en is None:
        # Nadie ordenó este borrado. Se rechaza en vez de aceptarlo: un cliente
        # con un bug podría marcar como borrado un equipo que sigue trabajando,
        # y la oficina lo daría por recuperado.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no hay orden de borrado para este dispositivo"
        )

    ahora = datetime.now(UTC)
    if dispositivo.borrado_confirmado_en is None:
        dispositivo.borrado_confirmado_en = ahora
        dispositivo.borrado_cola_al_confirmar = confirmacion.cola_pendiente
    # Idempotente: un reenvío no cambia la hora de la primera confirmación. Es
    # la misma regla que el resto del sistema — la red entrega dos veces.
    dispositivo.estado = "revocado"
    if dispositivo.revocado_en is None:
        dispositivo.revocado_en = ahora
        dispositivo.revocado_motivo = f"borrado remoto: {dispositivo.borrado_motivo}"
    await sesion.execute(
        text(
            "UPDATE sesiones SET revocada_en = now() "
            "WHERE dispositivo_id = :dev AND revocada_en IS NULL"
        ),
        {"dev": str(dispositivo.id)},
    )
    await sesion.commit()
