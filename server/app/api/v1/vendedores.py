"""Los vendedores, desde el teléfono de la oficina (`/v1/vendedores`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «quiero ver en la app el almacén de cada
vendedor, sus detalles, sus ventas y todo eso». Es la pantalla Vendedores del
panel, para quien está en la calle o en la bodega con el teléfono.

────────────────────────────────────────────────────────────────────────────
LAS MISMAS CONSULTAS QUE EL PANEL
────────────────────────────────────────────────────────────────────────────
La lista, la ficha y la línea de tiempo se importan de
`app/api/admin/vendedores.py`; el periodo, de `periodo.py`. Así la app y el
dashboard no pueden decir dos cosas distintas del mismo vendedor.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
`ventas.ver_todas`: admin, gerente y supervisor. El vendedor no lo tiene: la
venta de los demás y la cuenta de cada uno no son suyas. Eso incluye el camión
de otro, aunque el vendedor tenga `inventario.ver` para el suyo.

El camión que se muestra es lo que el SERVIDOR sabe: lo que el vendedor vendió
sin señal todavía no está restado. La respuesta trae la hora del último
contacto del teléfono para que la pantalla lo diga.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.admin.periodo import PERIODOS, Periodo, leer_periodo
from app.api.admin.vendedores import (
    LIMITE,
    PERMISO_VER,
    TIPOS,
    ficha_del_vendedor,
    lista_de_vendedores,
    movimientos_del_vendedor,
    telefonos_del_vendedor,
)
from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero

router = APIRouter(prefix="/vendedores", tags=["vendedores"])


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class PeriodoVisto(BaseModel):
    clave: str
    etiqueta: str
    desde: date
    hasta: date
    descripcion: str

    @classmethod
    def de(cls, p: Periodo) -> PeriodoVisto:
        return cls(
            clave=p.clave, etiqueta=p.etiqueta, desde=p.inicio, hasta=p.fin,
            descripcion=p.descripcion,
        )


class VendedorEnLista(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    activo: bool
    camion: str | None
    rutas: str | None
    ventas: int
    importe: Dinero
    # Lo vendido en efectivo: lo que entrega en el corte.
    efectivo: Dinero
    no_ventas: int
    mermas: int
    ultimo_contacto: datetime | None
    saldo_cuenta: Dinero


class ListaDeVendedores(BaseModel):
    periodo: PeriodoVisto
    periodos: list[tuple[str, str]]
    vendedores: list[VendedorEnLista]


class Ficha(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    activo: bool
    camion: str | None
    rutas: str | None
    saldo_cuenta: Dinero


class ResumenDeTipo(BaseModel):
    tipo: str
    etiqueta: str
    cuantos: int
    importe: Dinero


class Movimiento(BaseModel):
    momento: datetime | None
    fecha: date | None
    tipo: str
    etiqueta: str
    folio: str | None
    cliente: str | None
    detalle: str | None
    importe: Dinero | None
    estado: str | None
    marca: bool | None
    # El id del documento. La app lo usa para abrir el detalle de una venta.
    ref: str | None


class Telefono(BaseModel):
    etiqueta: str
    estado: str
    ultima_sync_push_en: datetime | None
    ultima_sync_pull_en: datetime | None
    cola_pendiente: int | None


class DetalleDeVendedor(BaseModel):
    vendedor: Ficha
    periodo: PeriodoVisto
    periodos: list[tuple[str, str]]
    resumen: list[ResumenDeTipo]
    movimientos: list[Movimiento]
    recortado: bool
    limite: int
    telefonos: list[Telefono]


class Existencia(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    cantidad: Cantidad


class CamionDelVendedor(BaseModel):
    vendedor: str
    camion: str | None
    existencias: list[Existencia]
    piezas: Cantidad
    # Cuándo habló el teléfono por última vez: lo que vendió después de eso, sin
    # señal, todavía no está restado aquí.
    ultimo_contacto: datetime | None


class Partida(BaseModel):
    linea: int
    sku: str
    nombre: str
    unidad: str
    cantidad: Cantidad
    precio_unitario: Dinero
    importe: Dinero


class VentaVista(BaseModel):
    id: uuid.UUID
    folio: str | None
    fecha_operativa: date
    momento: datetime | None
    vendedor: str
    cliente: str
    tipo: str
    # 'efectivo' | 'transferencia'; nula solo en las ventas a crédito del piloto.
    forma_pago: str | None = None
    # 'confirmado' | 'por_confirmar' | 'rechazado' (la transferencia que no llegó).
    pago_estado: str | None = None
    estado: str
    total: Dinero
    partidas: list[Partida]


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@router.get("", response_model=ListaDeVendedores)
async def lista(
    actor: ActorDep, sesion: SesionDep, periodo: str = "", desde: str = "", hasta: str = ""
) -> ListaDeVendedores:
    actor.exigir(PERMISO_VER)
    rango = leer_periodo(periodo, desde, hasta)
    filas = await lista_de_vendedores(sesion, rango)
    return ListaDeVendedores(
        periodo=PeriodoVisto.de(rango),
        periodos=list(PERIODOS),
        vendedores=[VendedorEnLista(**dict(f)) for f in filas],
    )


# Va antes de `/{vendedor_id}` para que «ventas» nunca se lea como un id.
@router.get("/ventas/{venta_id}", response_model=VentaVista)
async def venta(venta_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> VentaVista:
    """Una venta con lo que se vendió, renglón por renglón."""
    actor.exigir(PERMISO_VER)
    cabecera = (
        await sesion.execute(
            text(
                "SELECT v.id, v.folio_local AS folio, v.fecha_operativa, "
                "       v.fecha_dispositivo AS momento, v.tipo, v.forma_pago, "
                "       v.pago_estado, v.estado, v.total, "
                "       u.nombre AS vendedor, c.nombre_comercial AS cliente "
                "  FROM ventas v "
                "  JOIN usuarios u ON u.id = v.vendedor_id "
                "  JOIN clientes c ON c.id = v.cliente_id "
                " WHERE v.id = :v"
            ),
            {"v": venta_id},
        )
    ).mappings().first()
    if cabecera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa venta no existe")
    partidas = (
        await sesion.execute(
            text(
                """
                SELECT vp.linea, p.sku, p.nombre, vp.unidad_codigo AS unidad,
                       vp.cantidad, vp.precio_unitario, vp.importe
                  FROM venta_partidas vp
                  JOIN productos p ON p.id = vp.producto_id
                 WHERE vp.venta_id = :v
                 ORDER BY vp.linea
                """
            ),
            {"v": venta_id},
        )
    ).mappings().all()
    return VentaVista(**dict(cabecera), partidas=[Partida(**dict(p)) for p in partidas])


@router.get("/{vendedor_id}", response_model=DetalleDeVendedor)
async def detalle(
    vendedor_id: uuid.UUID,
    actor: ActorDep,
    sesion: SesionDep,
    periodo: str = "",
    desde: str = "",
    hasta: str = "",
    tipo: str = "",
) -> DetalleDeVendedor:
    """Su ficha y TODO lo que hizo en el periodo, en orden de hora."""
    actor.exigir(PERMISO_VER)
    rango = leer_periodo(periodo, desde, hasta)
    etiquetas = dict(TIPOS)
    if tipo not in etiquetas:
        tipo = ""

    ficha = await ficha_del_vendedor(sesion, vendedor_id)
    if ficha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese vendedor no existe")
    resumen, filas, recortado = await movimientos_del_vendedor(sesion, ficha, rango, tipo)
    telefonos = await telefonos_del_vendedor(sesion, vendedor_id)

    return DetalleDeVendedor(
        vendedor=Ficha(**{k: ficha[k] for k in Ficha.model_fields}),
        periodo=PeriodoVisto.de(rango),
        periodos=list(PERIODOS),
        # En el orden del día, como en el panel.
        resumen=[
            ResumenDeTipo(
                tipo=clave,
                etiqueta=etiqueta,
                cuantos=resumen[clave]["cuantos"],
                importe=resumen[clave]["importe"] or Decimal(0),
            )
            for clave, etiqueta in TIPOS
            if clave in resumen
        ],
        movimientos=[
            Movimiento(
                **{k: m[k] for k in Movimiento.model_fields if k != "etiqueta"},
                etiqueta=etiquetas.get(m["tipo"], m["tipo"]),
            )
            for m in filas
        ],
        recortado=recortado,
        limite=LIMITE,
        telefonos=[Telefono(**{k: t[k] for k in Telefono.model_fields}) for t in telefonos],
    )


@router.get("/{vendedor_id}/camion", response_model=CamionDelVendedor)
async def camion(vendedor_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> CamionDelVendedor:
    """Lo que trae su camión, según el servidor.

    Con los renglones en negativo incluidos: un negativo es la señal de que algo
    se vendió sin estar cargado, y esconderlo sería esconder el problema.
    """
    actor.exigir(PERMISO_VER)
    ficha = await ficha_del_vendedor(sesion, vendedor_id)
    if ficha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese vendedor no existe")

    existencias = []
    if ficha["almacen_id"] is not None:
        existencias = (
            await sesion.execute(
                text(
                    """
                    SELECT e.producto_id, p.sku, p.nombre, p.unidad_base, e.cantidad
                      FROM existencias e
                      JOIN productos p ON p.id = e.producto_id
                     WHERE e.almacen_id = :a AND e.cantidad <> 0
                     ORDER BY p.nombre
                    """
                ),
                {"a": ficha["almacen_id"]},
            )
        ).mappings().all()
    ultimo = (
        await sesion.execute(
            text(
                "SELECT max(GREATEST(ultima_sync_push_en, ultima_sync_pull_en)) "
                "  FROM dispositivos WHERE usuario_id = :v"
            ),
            {"v": vendedor_id},
        )
    ).scalar_one_or_none()

    return CamionDelVendedor(
        vendedor=ficha["nombre"],
        camion=ficha["camion"],
        existencias=[Existencia(**dict(e)) for e in existencias],
        piezas=sum((Decimal(e["cantidad"]) for e in existencias), Decimal(0)),
        ultimo_contacto=ultimo,
    )
