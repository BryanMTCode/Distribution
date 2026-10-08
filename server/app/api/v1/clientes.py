"""Clientes: la lista de la ruta, el alta y sus condiciones.

La operación es de contado (ADR 0002 §81): aquí ya no hay crédito, cartera ni
evaluación de crédito. Las columnas de crédito de `clientes` se quedan en la base
como historia del piloto, sin que nada las lea.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text

from app.api.deps import ROLES_DE_OFICINA, ActorDep, SesionDep
from app.api.esquemas import EntradaBase, EsquemaBase
from app.domain.identificadores import nuevo_id
from app.infra.models import Cliente

router = APIRouter(prefix="/clientes", tags=["clientes"])


class ClienteSalida(EsquemaBase):
    id: uuid.UUID
    codigo: str | None
    nombre_comercial: str
    telefono: str | None
    ruta_id: uuid.UUID | None
    secuencia: int | None
    lat: Decimal | None
    lng: Decimal | None
    ubicacion_origen: str | None
    estatus: str
    origen_alta: str
    requiere_revision: bool


class PaginaClientes(BaseModel):
    total: int
    clientes: list[ClienteSalida]


def _filtro_de_alcance(actor) -> list:
    """El *scope guard*: un vendedor solo ve los clientes de sus rutas.

    La UI oculta, el servidor prohíbe. Si el alcance se aplicara solo en la
    app, bastaría un token y `curl` para leer la cartera completa.
    """
    if actor.rol in ROLES_DE_OFICINA:
        return []
    if not actor.rutas:
        # Sin rutas asignadas no ve nada. Devolver todo sería el fallo abierto.
        return [text("false")]
    return [Cliente.ruta_id.in_(actor.rutas)]


@router.get("", response_model=PaginaClientes)
async def listar_clientes(
    actor: ActorDep,
    sesion: SesionDep,
    busqueda: str | None = Query(default=None, description="Nombre, código o teléfono"),
    ruta_id: uuid.UUID | None = None,
    limite: int = Query(default=100, ge=1, le=500),
    desplazamiento: int = Query(default=0, ge=0),
) -> PaginaClientes:
    actor.exigir("clientes.ver")

    filtros = [Cliente.estatus != "baja", *_filtro_de_alcance(actor)]
    if ruta_id is not None:
        if not actor.alcanza_ruta(ruta_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "esa ruta está fuera de tu alcance")
        filtros.append(Cliente.ruta_id == ruta_id)
    if busqueda:
        patron = f"%{busqueda.strip()}%"
        filtros.append(
            or_(
                Cliente.nombre_comercial.ilike(patron),
                Cliente.codigo.ilike(patron),
                Cliente.telefono.ilike(patron),
            )
        )

    total = (
        await sesion.execute(select(func.count()).select_from(Cliente).where(*filtros))
    ).scalar_one()
    clientes = list(
        (
            await sesion.execute(
                select(Cliente)
                .where(*filtros)
                .order_by(Cliente.secuencia.nulls_last(), Cliente.nombre_comercial)
                .limit(limite)
                .offset(desplazamiento)
            )
        ).scalars()
    )
    return PaginaClientes(
        total=total, clientes=[ClienteSalida.model_validate(c) for c in clientes]
    )


class ClienteEntrada(EntradaBase):
    """Alta de cliente.

    Las condiciones comerciales (lista de precios, ruta, estatus) son propiedad
    del servidor y se fijan con `clientes.administrar`, no al dar de alta desde
    la calle.
    """

    id: uuid.UUID | None = Field(
        default=None,
        description="UUID generado en el dispositivo cuando el alta es en campo",
    )
    nombre_comercial: str = Field(min_length=1, max_length=200)
    ruta_id: uuid.UUID | None = None
    canal_codigo: str | None = None
    contacto_nombre: str | None = None
    telefono: str | None = None
    calle: str | None = None
    numero: str | None = None
    colonia: str | None = None
    municipio: str | None = None
    referencias: str | None = None
    lat: Decimal | None = Field(default=None, ge=-90, le=90)
    lng: Decimal | None = Field(default=None, ge=-180, le=180)
    ubicacion_precision_m: Decimal | None = Field(default=None, ge=0)
    ubicacion_origen: str | None = Field(
        default=None, description="'gps' o 'manual' si el vendedor ajustó las coordenadas"
    )


@router.post("", response_model=ClienteSalida, status_code=status.HTTP_201_CREATED)
async def crear_cliente(
    entrada: ClienteEntrada, actor: ActorDep, sesion: SesionDep
) -> ClienteSalida:
    """Idempotente cuando el alta trae su UUID del dispositivo.

    El mismo principio que el resto del sistema: la red puede entregar dos
    veces y recibirlo dos veces no debe crear dos clientes.
    """
    actor.exigir("clientes.crear")

    if entrada.ruta_id is not None and not actor.alcanza_ruta(entrada.ruta_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "esa ruta está fuera de tu alcance")

    if entrada.ubicacion_origen not in (None, "gps", "manual"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "ubicacion_origen debe ser 'gps' o 'manual'"
        )

    if entrada.id is not None:
        existente = await sesion.get(Cliente, entrada.id)
        if existente is not None:
            return ClienteSalida.model_validate(existente)

    de_campo = entrada.id is not None
    ahora = datetime.now(UTC)
    cliente = Cliente(
        id=entrada.id or nuevo_id(),
        nombre_comercial=entrada.nombre_comercial.strip(),
        ruta_id=entrada.ruta_id or (next(iter(actor.rutas), None) if de_campo else None),
        canal_codigo=entrada.canal_codigo,
        contacto_nombre=entrada.contacto_nombre,
        telefono=entrada.telefono,
        calle=entrada.calle,
        numero=entrada.numero,
        colonia=entrada.colonia,
        municipio=entrada.municipio,
        referencias=entrada.referencias,
        lat=entrada.lat,
        lng=entrada.lng,
        ubicacion_precision_m=entrada.ubicacion_precision_m,
        ubicacion_origen=entrada.ubicacion_origen,
        ubicacion_capturada_en=ahora if entrada.lat is not None else None,
        # Las columnas de crédito son historia del piloto (ADR 0002 §81): un
        # cliente nuevo nace sin él, y nada lo abre.
        permite_credito=False,
        limite_credito=Decimal("0"),
        dias_credito=0,
        bloqueado=False,
        estatus="prospecto" if de_campo else "activo",
        origen_alta="campo" if de_campo else "oficina",
        creado_por=actor.usuario_id,
        dispositivo_id=actor.dispositivo_id if de_campo else None,
        creado_en=ahora,
        actualizado_en=ahora,
    )
    sesion.add(cliente)
    await sesion.commit()
    return ClienteSalida.model_validate(cliente)


class CondicionesEntrada(EntradaBase):
    """Condiciones comerciales: propiedad exclusiva del servidor."""

    lista_precios_id: uuid.UUID | None = None
    ruta_id: uuid.UUID | None = None
    secuencia: int | None = Field(default=None, ge=0)
    estatus: str | None = None


@router.patch("/{cliente_id}/condiciones", response_model=ClienteSalida)
async def fijar_condiciones(
    cliente_id: uuid.UUID, entrada: CondicionesEntrada, actor: ActorDep, sesion: SesionDep
) -> ClienteSalida:
    actor.exigir("clientes.administrar")
    cliente = await sesion.get(Cliente, cliente_id)
    if cliente is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente no encontrado")

    cambios = entrada.model_dump(exclude_unset=True)
    if (estatus := cambios.get("estatus")) and estatus not in (
        "prospecto", "activo", "inactivo", "baja"
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"estatus inválido: {estatus}")

    for campo, valor in cambios.items():
        setattr(cliente, campo, valor)
    cliente.actualizado_en = datetime.now(UTC)
    await sesion.commit()
    return ClienteSalida.model_validate(cliente)
