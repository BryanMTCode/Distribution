"""Catálogo: productos, presentaciones y precios."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad, EntradaBase, EsquemaBase
from app.domain.catalogo import UnidadesInvalidas, UnidadVendible, validar_unidades
from app.domain.identificadores import nuevo_id
from app.infra.models import ListaPrecios, Precio, Producto, ProductoUnidad, UnidadMedida

router = APIRouter(prefix="/catalogo", tags=["catálogo"])


# ---------------------------------------------------------------------------
# Unidades
# ---------------------------------------------------------------------------

class UnidadSalida(EsquemaBase):
    codigo: str
    nombre: str
    fraccionable: bool


@router.get("/unidades", response_model=list[UnidadSalida])
async def listar_unidades(actor: ActorDep, sesion: SesionDep) -> list[UnidadMedida]:
    actor.exigir("catalogo.ver")
    filas = await sesion.execute(select(UnidadMedida).order_by(UnidadMedida.codigo))
    return list(filas.scalars())


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------

class UnidadProducto(EntradaBase):
    codigo: str = Field(description="Código de la unidad: 'PZA', 'CAJA'")
    factor: Cantidad = Field(description="Cuántas unidades base contiene")
    es_default: bool = False


class ProductoEntrada(EntradaBase):
    sku: str = Field(min_length=1, max_length=40)
    nombre: str = Field(min_length=1, max_length=200)
    unidad_base: str
    unidades: list[UnidadProducto] = Field(min_length=1)
    codigo_barras: str | None = None
    descripcion: str | None = None
    categoria_id: uuid.UUID | None = None
    marca_id: uuid.UUID | None = None
    peso_gramos: int | None = Field(default=None, ge=0)
    tasa_iva: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    @field_validator("sku")
    @classmethod
    def sku_normalizado(cls, v: str) -> str:
        # El SKU se teclea y se escanea; los espacios y las minúsculas producen
        # duplicados que nadie ve hasta que el inventario no cuadra.
        return v.strip().upper()


class UnidadProductoSalida(EsquemaBase):
    unidad_codigo: str
    factor: Cantidad
    es_default: bool


class ProductoSalida(EsquemaBase):
    id: uuid.UUID
    sku: str
    nombre: str
    codigo_barras: str | None
    unidad_base: str
    tasa_iva: Decimal
    activo: bool
    unidades: list[UnidadProductoSalida]


class PaginaProductos(BaseModel):
    total: int
    productos: list[ProductoSalida]


@router.get("/productos", response_model=PaginaProductos)
async def listar_productos(
    actor: ActorDep,
    sesion: SesionDep,
    busqueda: str | None = Query(default=None, description="Texto, SKU o código de barras"),
    solo_activos: bool = True,
    limite: int = Query(default=100, ge=1, le=500),
    desplazamiento: int = Query(default=0, ge=0),
) -> PaginaProductos:
    actor.exigir("catalogo.ver")

    filtros = []
    if solo_activos:
        filtros.append(Producto.activo.is_(True))
    if busqueda:
        patron = f"%{busqueda.strip()}%"
        filtros.append(
            or_(
                Producto.nombre.ilike(patron),
                Producto.sku.ilike(patron),
                Producto.codigo_barras == busqueda.strip(),
            )
        )

    total = (
        await sesion.execute(select(func.count()).select_from(Producto).where(*filtros))
    ).scalar_one()
    productos = list(
        (
            await sesion.execute(
                select(Producto)
                .where(*filtros)
                .order_by(Producto.nombre)
                .limit(limite)
                .offset(desplazamiento)
            )
        ).scalars()
    )
    return PaginaProductos(
        total=total, productos=[ProductoSalida.model_validate(p) for p in productos]
    )


@router.post("/productos", response_model=ProductoSalida, status_code=status.HTTP_201_CREATED)
async def crear_producto(
    entrada: ProductoEntrada, actor: ActorDep, sesion: SesionDep
) -> ProductoSalida:
    actor.exigir("catalogo.administrar")

    try:
        validar_unidades(
            entrada.unidad_base,
            [UnidadVendible(u.codigo, u.factor, u.es_default) for u in entrada.unidades],
        )
    except UnidadesInvalidas as e:
        # 422 y no 400: es una entidad bien formada con contenido inválido.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    codigos = {u.codigo for u in entrada.unidades}
    existentes = set(
        (
            await sesion.execute(
                select(UnidadMedida.codigo).where(UnidadMedida.codigo.in_(codigos))
            )
        ).scalars()
    )
    if faltantes := codigos - existentes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unidades desconocidas: {', '.join(sorted(faltantes))}",
        )

    ahora = datetime.now(UTC)
    producto = Producto(
        id=nuevo_id(),
        sku=entrada.sku,
        nombre=entrada.nombre,
        codigo_barras=entrada.codigo_barras,
        descripcion=entrada.descripcion,
        categoria_id=entrada.categoria_id,
        marca_id=entrada.marca_id,
        unidad_base=entrada.unidad_base,
        peso_gramos=entrada.peso_gramos,
        tasa_iva=entrada.tasa_iva,
        tasa_ieps=Decimal("0"),
        maneja_lote=False,
        activo=True,
        creado_en=ahora,
        actualizado_en=ahora,
    )
    producto.unidades = [
        ProductoUnidad(
            unidad_codigo=u.codigo, factor=u.factor, es_default=u.es_default, activo=True
        )
        for u in entrada.unidades
    ]
    sesion.add(producto)
    try:
        await sesion.commit()
    except IntegrityError as e:
        await sesion.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"ya existe un producto con el SKU {entrada.sku}"
        ) from e
    return ProductoSalida.model_validate(producto)


class ProductoParche(EntradaBase):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    codigo_barras: str | None = None
    descripcion: str | None = None
    tasa_iva: Decimal | None = Field(default=None, ge=0, le=1)
    activo: bool | None = None


@router.patch("/productos/{producto_id}", response_model=ProductoSalida)
async def editar_producto(
    producto_id: uuid.UUID, parche: ProductoParche, actor: ActorDep, sesion: SesionDep
) -> ProductoSalida:
    """No permite cambiar `unidad_base` ni los factores.

    Cambiarlos reinterpretaría todos los movimientos de inventario ya
    registrados: 500 unidades base pasarían a significar otra cosa. Si una
    presentación cambia de tamaño, se da de alta un producto nuevo.
    """
    actor.exigir("catalogo.administrar")
    producto = await sesion.get(Producto, producto_id)
    if producto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "producto no encontrado")

    for campo, valor in parche.model_dump(exclude_unset=True).items():
        setattr(producto, campo, valor)
    producto.actualizado_en = datetime.now(UTC)
    await sesion.commit()
    return ProductoSalida.model_validate(producto)


# ---------------------------------------------------------------------------
# Listas de precios
# ---------------------------------------------------------------------------

class ListaSalida(EsquemaBase):
    id: uuid.UUID
    codigo: str
    nombre: str
    es_default: bool
    activo: bool


@router.get("/listas-precios", response_model=list[ListaSalida])
async def listar_listas(actor: ActorDep, sesion: SesionDep) -> list[ListaPrecios]:
    actor.exigir("catalogo.ver")
    return list(
        (
            await sesion.execute(
                select(ListaPrecios).where(ListaPrecios.activo.is_(True)).order_by(
                    ListaPrecios.codigo
                )
            )
        ).scalars()
    )


class PrecioEntrada(EntradaBase):
    producto_id: uuid.UUID
    unidad_codigo: str
    precio: Decimal = Field(ge=0, description="Precio por presentación")
    precio_minimo: Decimal | None = Field(default=None, ge=0)


class PrecioSalida(EsquemaBase):
    producto_id: uuid.UUID
    unidad_codigo: str
    precio: Decimal
    precio_minimo: Decimal | None
    version: int


@router.put("/listas-precios/{lista_id}/precios", response_model=list[PrecioSalida])
async def fijar_precios(
    lista_id: uuid.UUID,
    entradas: list[PrecioEntrada],
    actor: ActorDep,
    sesion: SesionDep,
) -> list[PrecioSalida]:
    """Alta o actualización de precios en lote.

    Cada cambio incrementa `version`. Ese número viaja con cada venta, y es lo
    que después permite saber que una venta se hizo con una lista vieja sin
    tener que comparar importes partida por partida — la venta no se rechaza
    por eso, se marca para revisión (§0.1).
    """
    actor.exigir("catalogo.administrar")

    lista = await sesion.get(ListaPrecios, lista_id)
    if lista is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lista de precios no encontrada")
    if not entradas:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no hay precios que fijar")

    claves = {(e.producto_id, e.unidad_codigo) for e in entradas}
    if len(claves) != len(entradas):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "el lote trae el mismo producto y unidad dos veces",
        )

    # La presentación debe existir para ese producto: un precio sobre una
    # unidad que el producto no vende es un precio que nadie podrá aplicar.
    validas = {
        (pid, cod)
        for pid, cod in (
            await sesion.execute(
                select(ProductoUnidad.producto_id, ProductoUnidad.unidad_codigo).where(
                    ProductoUnidad.producto_id.in_({e.producto_id for e in entradas})
                )
            )
        ).all()
    }
    if invalidas := claves - validas:
        detalle = ", ".join(f"{p}/{u}" for p, u in sorted(invalidas, key=str))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"esas presentaciones no existen para el producto: {detalle}",
        )

    ahora = datetime.now(UTC)
    salida: list[PrecioSalida] = []
    for entrada in entradas:
        if entrada.precio_minimo is not None and entrada.precio_minimo > entrada.precio:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "el precio mínimo supera al precio en "
                f"{entrada.producto_id}/{entrada.unidad_codigo}",
            )
        existente = await sesion.get(
            Precio, {"lista_id": lista_id, "producto_id": entrada.producto_id,
                     "unidad_codigo": entrada.unidad_codigo}
        )
        if existente is None:
            existente = Precio(
                lista_id=lista_id,
                producto_id=entrada.producto_id,
                unidad_codigo=entrada.unidad_codigo,
                precio=entrada.precio,
                precio_minimo=entrada.precio_minimo,
                version=1,
                actualizado_en=ahora,
            )
            sesion.add(existente)
        elif existente.precio != entrada.precio or existente.precio_minimo != entrada.precio_minimo:
            existente.precio = entrada.precio
            existente.precio_minimo = entrada.precio_minimo
            existente.version += 1      # solo si algo cambió de verdad
            existente.actualizado_en = ahora
        salida.append(
            PrecioSalida(
                producto_id=existente.producto_id,
                unidad_codigo=existente.unidad_codigo,
                precio=existente.precio,
                precio_minimo=existente.precio_minimo,
                version=existente.version,
            )
        )

    await sesion.commit()
    return salida


@router.get("/listas-precios/{lista_id}/precios", response_model=list[PrecioSalida])
async def listar_precios(
    lista_id: uuid.UUID, actor: ActorDep, sesion: SesionDep
) -> list[Precio]:
    actor.exigir("catalogo.ver")
    return list(
        (await sesion.execute(select(Precio).where(Precio.lista_id == lista_id))).scalars()
    )
