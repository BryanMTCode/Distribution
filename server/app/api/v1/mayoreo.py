"""Ventas de mayoreo desde la app de la oficina (ADR 0002 §105).

«El administrador puede hacer ventas a mayoreo al precio que él decida, y los
clientes él los maneja.» En línea, como las cargas y las entradas de la app: la
venta sale de la bodega en ese momento. La lógica vive en
`app/infra/mayoreo.py`, la misma que usa el panel.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.admin.periodo import periodo_pedido
from app.api.deps import ROLES_DE_OFICINA, Actor, ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero
from app.infra import mayoreo
from app.infra.mayoreo import MayoreoInvalido, RenglonPedido

router = APIRouter(prefix="/mayoreo", tags=["mayoreo"])


def _exigir(actor: Actor) -> None:
    actor.exigir(mayoreo.PERMISO)
    if actor.rol not in ROLES_DE_OFICINA:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "falta el permiso de la oficina")


class ClienteMayoreo(BaseModel):
    id: uuid.UUID
    codigo: str | None
    nombre_comercial: str
    telefono: str | None = None
    direccion: str | None = None
    ventas: int = 0
    vendido: Dinero = Decimal(0)
    ultima: date | None = None


class Bodega(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str


class Presentacion(BaseModel):
    unidad: str
    factor: Cantidad
    precio_lista: Dinero | None = None


class ProductoEnBodega(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    familia: str | None
    existencia: Cantidad
    presentaciones: list[Presentacion]


class Opciones(BaseModel):
    clientes: list[ClienteMayoreo]
    bodegas: list[Bodega]


class VentaResumen(BaseModel):
    id: uuid.UUID
    folio: str
    fecha: date
    estado: str
    total: Dinero
    forma_pago: str
    cliente: str
    bodega: str
    vendida_por: str | None
    renglones: int
    piezas: Cantidad


class VentasDelPeriodo(BaseModel):
    desde: date
    hasta: date
    ventas: list[VentaResumen]
    total: Dinero


class Partida(BaseModel):
    sku: str
    nombre: str
    unidad_codigo: str
    bultos: Cantidad
    cantidad: Cantidad
    precio: Dinero
    precio_lista: Dinero | None
    importe: Dinero


class Venta(BaseModel):
    id: uuid.UUID
    folio: str
    fecha: date
    estado: str
    cliente: str
    bodega: str
    forma_pago: str
    referencia_pago: str | None
    total: Dinero
    nota: str | None
    motivo_cancelacion: str | None
    partidas: list[Partida]
    ticket: str


class PeticionCliente(BaseModel):
    nombre: str = Field(min_length=1, max_length=200)
    telefono: str | None = Field(default=None, max_length=40)
    calle: str | None = Field(default=None, max_length=200)
    colonia: str | None = Field(default=None, max_length=120)
    referencias: str | None = Field(default=None, max_length=400)


class PedidoDeRenglon(BaseModel):
    producto_id: uuid.UUID
    unidad: str = Field(max_length=20)
    bultos: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    precio: Decimal = Field(gt=0, max_digits=14, decimal_places=4)


class PeticionVenta(BaseModel):
    cliente_id: uuid.UUID
    almacen_id: uuid.UUID
    renglones: list[PedidoDeRenglon] = Field(min_length=1, max_length=mayoreo.RENGLONES_MAXIMOS)
    forma_pago: str = "efectivo"
    referencia_pago: str | None = Field(default=None, max_length=80)
    nota: str | None = Field(default=None, max_length=600)


class PeticionCancelar(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)


def _venta(v: dict) -> Venta:
    return Venta(
        id=v["id"], folio=v["folio"], fecha=v["fecha"], estado=v["estado"],
        cliente=v["cliente"], bodega=v["bodega"], forma_pago=v["forma_pago"],
        referencia_pago=v["referencia_pago"], total=v["total"], nota=v["nota"],
        motivo_cancelacion=v["motivo_cancelacion"],
        partidas=[Partida(**{k: p[k] for k in Partida.model_fields}) for p in v["partidas"]],
        ticket=mayoreo.ticket(v),
    )


@router.get("/opciones", response_model=Opciones)
async def opciones(actor: ActorDep, sesion: SesionDep) -> Opciones:
    _exigir(actor)
    return Opciones(
        clientes=[ClienteMayoreo(**c) for c in await mayoreo.clientes_de_mayoreo(sesion)],
        bodegas=[Bodega(**b) for b in await mayoreo.bodegas(sesion)],
    )


@router.get("/surtido/{almacen_id}", response_model=list[ProductoEnBodega])
async def surtido(almacen_id: uuid.UUID, actor: ActorDep, sesion: SesionDep):
    _exigir(actor)
    return [ProductoEnBodega(**p) for p in await mayoreo.surtido(sesion, almacen_id)]


@router.post("/clientes", response_model=ClienteMayoreo, status_code=status.HTTP_201_CREATED)
async def alta_de_cliente(peticion: PeticionCliente, actor: ActorDep, sesion: SesionDep):
    _exigir(actor)
    try:
        nuevo = await mayoreo.alta_de_cliente(
            sesion, nombre=peticion.nombre, telefono=peticion.telefono, calle=peticion.calle,
            colonia=peticion.colonia, referencias=peticion.referencias, quien=actor.usuario_id,
        )
    except MayoreoInvalido as e:
        await sesion.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    await sesion.commit()
    return ClienteMayoreo(**nuevo)


@router.get("", response_model=VentasDelPeriodo)
async def ventas(
    actor: ActorDep, sesion: SesionDep, periodo: str = "hoy", desde: str = "", hasta: str = ""
) -> VentasDelPeriodo:
    _exigir(actor)
    elegido = await periodo_pedido(sesion, periodo, desde, hasta or desde)
    filas = await mayoreo.ventas_del_periodo(sesion, elegido.inicio, elegido.fin)
    return VentasDelPeriodo(
        desde=elegido.inicio,
        hasta=elegido.fin,
        ventas=[VentaResumen(**f) for f in filas],
        total=sum((Decimal(f["total"]) for f in filas if f["estado"] == "confirmada"), Decimal(0)),
    )


@router.post("", response_model=Venta, status_code=status.HTTP_201_CREATED)
async def vender(peticion: PeticionVenta, actor: ActorDep, sesion: SesionDep) -> Venta:
    _exigir(actor)
    try:
        venta_id, _ = await mayoreo.vender(
            sesion,
            cliente_id=peticion.cliente_id,
            almacen_id=peticion.almacen_id,
            renglones=[
                RenglonPedido(r.producto_id, r.unidad, r.bultos, r.precio)
                for r in peticion.renglones
            ],
            forma_pago=peticion.forma_pago,
            referencia_pago=peticion.referencia_pago,
            nota=peticion.nota,
            quien=actor.usuario_id,
        )
    except MayoreoInvalido as e:
        await sesion.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    await sesion.commit()
    return _venta(await mayoreo.detalle(sesion, venta_id))


@router.get("/{venta_id}", response_model=Venta)
async def ver(venta_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Venta:
    _exigir(actor)
    venta = await mayoreo.detalle(sesion, venta_id)
    if venta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa venta no existe.")
    return _venta(venta)


@router.post("/{venta_id}/cancelar", response_model=Venta)
async def cancelar(
    venta_id: uuid.UUID, peticion: PeticionCancelar, actor: ActorDep, sesion: SesionDep
) -> Venta:
    _exigir(actor)
    try:
        await mayoreo.cancelar(sesion, venta_id, motivo=peticion.motivo, quien=actor.usuario_id)
    except MayoreoInvalido as e:
        await sesion.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    await sesion.commit()
    return _venta(await mayoreo.detalle(sesion, venta_id))
