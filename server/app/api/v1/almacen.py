"""El almacén desde la app de la oficina (`/v1/almacen`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «quiero agregar mercancía y traspasar entre
almacenes desde la app: manejar todo el negocio en modo gerencia». Aquí viven:

· **Existencias**: qué hay en cada bodega y en cada camión.
· **Entradas**: la mercancía que llega (compra, inventario inicial, ajuste por
  conteo), con las MISMAS funciones que el panel (`app/api/admin/entradas.py`):
  costo por bulto, promedio ponderado y cuenta por pagar al proveedor.
· **Traspasos** entre bodegas (`app/infra/traspasos.py`).
· **Compras** que el gerente recibe en la calle (§83): se capturan sin señal y
  llegan de un jalón, con el id del teléfono, a la bodega principal.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
Ver: `inventario.ver` y ser de la oficina —el vendedor tiene `inventario.ver`
para SU camión, no para todos los almacenes—. Escribir (entradas y traspasos):
`inventario.ajustar`, el mismo permiso que las entradas del panel (supervisor,
gerente y admin).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.admin.comun import CapturaInvalida
from app.api.admin.entradas import (
    PERMISO as PERMISO_AJUSTAR,
)
from app.api.admin.entradas import (
    EntradaNoExiste,
    EntradaRechazada,
    _bodegas,
    _proveedores,
    abrir_entrada,
    agregar_renglon_a_entrada,
    cancelar_entrada,
    confirmar_entrada,
    datos_de_la_entrada,
    entradas_recientes,
    quitar_renglon_de_entrada,
)
from app.api.deps import ROLES_DE_OFICINA, Actor, ActorDep, SesionDep
from app.api.esquemas import Cantidad, Dinero
from app.domain.entradas import MOTIVOS, etiqueta
from app.domain.importes import CantidadInvalida
from app.infra.cierre_del_vendedor import bodega_principal
from app.infra.traspasos import (
    PedidoDeTraspaso,
    TraspasoRechazado,
    traspasar_entre_bodegas,
    traspasos_entre_bodegas,
)
from app.infra.valor_de_inventario import precio_de_venta

router = APIRouter(prefix="/almacen", tags=["almacen"])


def _exigir_ver(actor: Actor) -> None:
    actor.exigir("inventario.ver")
    if actor.rol not in ROLES_DE_OFICINA:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "falta el permiso de la oficina")


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class AlmacenResumen(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    tipo: str
    responsable: str | None
    productos: int
    piezas: Cantidad
    negativos: int
    # Lo que vale lo que tiene, a precio de venta (lo negativo no suma).
    valor: Dinero = Decimal(0)


class Presentacion(BaseModel):
    unidad: str
    factor: Cantidad


class Existencia(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    cantidad: Cantidad
    presentaciones: list[Presentacion]
    # Precio de venta de la pieza (a centavos, para mostrarlo) y lo que vale la
    # existencia. Nulos si el artículo no tiene precio en ninguna lista.
    precio: Dinero | None = None
    valor: Dinero | None = None
    # La familia del artículo (§89); nula si no tiene. Vienen ordenadas por
    # familia —en el orden de la hoja de la dirección— y luego por clave.
    familia: str | None = None


class ExistenciasDelAlmacen(BaseModel):
    almacen: AlmacenResumen
    existencias: list[Existencia]


class ProductoParaCapturar(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    maneja_lote: bool
    # En el almacén que se pidió: para traspasar, lo que hay; para recibir, lo
    # que ya había.
    existencia: Cantidad
    presentaciones: list[Presentacion]


class Opcion(BaseModel):
    id: uuid.UUID
    codigo: str | None = None
    nombre: str


class MotivoDeEntrada(BaseModel):
    clave: str
    etiqueta: str
    explicacion: str


class EntradaEnLista(BaseModel):
    id: uuid.UUID
    folio: str
    motivo: str
    motivo_etiqueta: str
    estado: str
    proveedor: str | None
    referencia: str | None
    fecha_operativa: date
    bodega: str
    renglones: int
    piezas: Cantidad
    importe_total: Dinero | None


class Entradas(BaseModel):
    entradas: list[EntradaEnLista]
    bodegas: list[Opcion]
    proveedores: list[Opcion]
    motivos: list[MotivoDeEntrada]


class RenglonDeEntrada(BaseModel):
    id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    cantidad: Cantidad
    unidad_codigo: str | None
    unidades_capturadas: Cantidad | None
    costo_unitario: Decimal | None
    importe: Dinero | None
    lote: str | None
    existencia: Cantidad
    proyectado: Cantidad


class CuentaPorPagar(BaseModel):
    proveedor: str
    importe_original: Dinero
    saldo: Dinero
    estado: str
    fecha_vencimiento: date


class Entrada(BaseModel):
    id: uuid.UUID
    folio: str
    motivo: str
    motivo_etiqueta: str
    estado: str
    editable: bool
    exige_costo: bool
    proveedor: str | None
    referencia: str | None
    nota: str | None
    fecha_operativa: date
    bodega: str
    almacen_destino_id: uuid.UUID
    renglones: list[RenglonDeEntrada]
    total_piezas: Cantidad
    importe_capturado: Dinero
    sin_costo: int
    cuenta: CuentaPorPagar | None
    mensaje: str | None = None


class PeticionEntrada(BaseModel):
    almacen_destino_id: uuid.UUID
    motivo: str = Field(max_length=20)
    proveedor_id: uuid.UUID | None = None
    proveedor: str = Field(default="", max_length=160)
    referencia: str = Field(default="", max_length=80)
    nota: str = Field(default="", max_length=500)


class PeticionRenglonDeEntrada(BaseModel):
    # El SKU, como en el panel: el producto se elige de la lista de la app.
    sku: str = Field(max_length=60)
    unidad: str = Field(max_length=20)
    # Texto: se valida con las reglas del panel (bultos enteros, costo > 0).
    cantidad: str = Field(max_length=20)
    costo: str = Field(default="", max_length=20)
    lote: str = Field(default="", max_length=40)
    caducidad: str = Field(default="", max_length=10)


class RenglonDeCompra(BaseModel):
    producto_id: uuid.UUID
    unidad: str = Field(max_length=20)
    # Bultos enteros, como texto: se validan con las reglas del panel.
    cantidad: str = Field(max_length=20)
    # Por bulto. Vacío: este renglón solo suma inventario (§83).
    costo: str = Field(default="", max_length=20)


class PeticionCompra(BaseModel):
    # El que le dio el teléfono al capturarla: la llave para no sumarla dos veces.
    id: uuid.UUID
    proveedor_id: uuid.UUID | None = None
    proveedor: str = Field(default="", max_length=160)
    referencia: str = Field(default="", max_length=80)
    nota: str = Field(default="", max_length=500)
    # El día en que llegó la mercancía (`YYYY-MM-DD`), aunque se mande después.
    fecha: str = Field(default="", max_length=10)
    renglones: list[RenglonDeCompra] = Field(min_length=1, max_length=300)


class PeticionCancelar(BaseModel):
    motivo: str = Field(max_length=300)


class TraspasoEnLista(BaseModel):
    id: uuid.UUID
    folio: str | None
    creado_en: datetime
    origen: str
    destino: str
    quien: str | None
    renglones: int
    piezas: Cantidad
    observaciones: str | None


class Traspasos(BaseModel):
    traspasos: list[TraspasoEnLista]
    bodegas: list[Opcion]


class PedidoDeTraspasoEntrada(BaseModel):
    producto_id: uuid.UUID
    unidad: str = Field(max_length=20)
    cantidad: str = Field(max_length=20)


class PeticionTraspaso(BaseModel):
    origen_id: uuid.UUID
    destino_id: uuid.UUID
    renglones: list[PedidoDeTraspasoEntrada] = Field(min_length=1, max_length=300)
    nota: str = Field(default="", max_length=500)


class TraspasoHecho(BaseModel):
    id: uuid.UUID
    folio: str
    mensaje: str


# ---------------------------------------------------------------------------
# Existencias
# ---------------------------------------------------------------------------
SQL_ALMACENES = f"""
SELECT a.id, a.codigo, a.nombre, a.tipo, u.nombre AS responsable,
       COALESCE(x.productos, 0) AS productos, COALESCE(x.piezas, 0) AS piezas,
       COALESCE(x.negativos, 0) AS negativos, COALESCE(x.valor, 0) AS valor
  FROM almacenes a
  LEFT JOIN usuarios u ON u.id = a.responsable_id
  LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE e.cantidad <> 0) AS productos,
               sum(e.cantidad) FILTER (WHERE e.cantidad > 0) AS piezas,
               count(*) FILTER (WHERE e.cantidad < 0) AS negativos,
               sum(round(e.cantidad * pv.precio, 2)) FILTER (WHERE e.cantidad > 0) AS valor
          FROM existencias e
          {precio_de_venta("e.producto_id")}
         WHERE e.almacen_id = a.id
  ) x ON true
 WHERE a.activo AND a.tipo IN ('bodega', 'camion')
"""  # noqa: S608 — fragmento constante del código

SQL_PRESENTACIONES = """
LEFT JOIN LATERAL (
      SELECT json_agg(json_build_object('unidad', pu.unidad_codigo,
                                        'factor', pu.factor::text)
                      ORDER BY pu.factor DESC) AS presentaciones
        FROM producto_unidades pu
       WHERE pu.producto_id = p.id AND pu.activo
) pres ON true
"""


def _presentaciones(crudo) -> list[Presentacion]:
    return [
        Presentacion(unidad=x["unidad"], factor=Decimal(x["factor"])) for x in (crudo or [])
    ]


@router.get("", response_model=list[AlmacenResumen])
async def almacenes(actor: ActorDep, sesion: SesionDep) -> list[AlmacenResumen]:
    """Las bodegas primero, luego los camiones; con lo que tiene cada uno."""
    _exigir_ver(actor)
    filas = (
        await sesion.execute(
            text(SQL_ALMACENES + " ORDER BY (a.tipo = 'bodega') DESC, a.nombre")
        )
    ).mappings().all()
    return [AlmacenResumen(**dict(f)) for f in filas]


@router.get("/{almacen_id}/existencias", response_model=ExistenciasDelAlmacen)
async def existencias(
    almacen_id: uuid.UUID, actor: ActorDep, sesion: SesionDep, q: str = ""
) -> ExistenciasDelAlmacen:
    """Lo que hay, producto por producto. Los negativos también: esconderlos sería
    esconder el problema."""
    _exigir_ver(actor)
    almacen = (
        await sesion.execute(text(SQL_ALMACENES + " AND a.id = :a"), {"a": almacen_id})
    ).mappings().first()
    if almacen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese almacén no existe")
    parametros: dict[str, object] = {"a": almacen_id}
    filtro = ""
    if q.strip():
        filtro = " AND (p.nombre ILIKE :q OR p.sku ILIKE :q OR p.codigo_barras = :exacto)"
        parametros.update({"q": f"%{q.strip()}%", "exacto": q.strip()})
    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT e.producto_id, p.sku, p.nombre, p.unidad_base, e.cantidad,
                       pres.presentaciones,
                       pv.precio, round(e.cantidad * pv.precio, 2) AS valor,
                       cat.nombre AS familia
                  FROM existencias e
                  JOIN productos p ON p.id = e.producto_id
                  LEFT JOIN categorias cat ON cat.id = p.categoria_id
                  {SQL_PRESENTACIONES}
                  {precio_de_venta("p.id")}
                 WHERE e.almacen_id = :a AND e.cantidad <> 0 {filtro}
                 ORDER BY (cat.id IS NULL), cat.orden, cat.nombre, p.sku
                 LIMIT 1000
                """  # noqa: S608 — fragmentos constantes del código
            ),
            parametros,
        )
    ).mappings().all()
    return ExistenciasDelAlmacen(
        almacen=AlmacenResumen(**dict(almacen)),
        existencias=[
            Existencia(
                **{k: f[k] for k in ("producto_id", "sku", "nombre", "unidad_base", "cantidad",
                                     "precio", "valor", "familia")},
                presentaciones=_presentaciones(f["presentaciones"]),
            )
            for f in filas
        ],
    )


class ArticuloDelCatalogo(BaseModel):
    producto_id: uuid.UUID
    sku: str
    nombre: str
    familia: str | None = None
    unidad_base: str
    # Por pieza, de la lista por omisión; nulo si no tiene precio.
    precio: Dinero | None = None
    en_bodegas: Cantidad
    en_camiones: Cantidad


@router.get("/articulos", response_model=list[ArticuloDelCatalogo])
async def articulos(actor: ActorDep, sesion: SesionDep) -> list[ArticuloDelCatalogo]:
    """Los artículos activos, por familia, con su precio y lo que hay de cada uno.

    Lo que abre «artículos activos» en la pantalla Empresa de la app (ADR 0002
    §97): la lista del catálogo, no la de un almacén.
    """
    _exigir_ver(actor)
    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_base,
                       cat.nombre AS familia, pv.precio,
                       COALESCE(sum(e.cantidad) FILTER (WHERE a.tipo = 'bodega'), 0)
                         AS en_bodegas,
                       COALESCE(sum(e.cantidad) FILTER (WHERE a.tipo = 'camion'), 0)
                         AS en_camiones
                  FROM productos p
                  LEFT JOIN categorias cat ON cat.id = p.categoria_id
                  LEFT JOIN existencias e ON e.producto_id = p.id
                  LEFT JOIN almacenes a ON a.id = e.almacen_id
                  {precio_de_venta("p.id")}
                 WHERE p.activo
                 GROUP BY p.id, cat.id, pv.precio
                 ORDER BY (cat.id IS NULL), cat.orden, cat.nombre, p.sku
                 LIMIT 2000
                """  # noqa: S608 — fragmentos constantes del código
            )
        )
    ).mappings().all()
    return [ArticuloDelCatalogo(**dict(f)) for f in filas]


@router.get("/productos", response_model=list[ProductoParaCapturar])
async def productos(
    actor: ActorDep, sesion: SesionDep, almacen_id: uuid.UUID, solo_con_existencia: bool = False
) -> list[ProductoParaCapturar]:
    """El catálogo activo, con lo que hay de cada producto en ese almacén.

    Para RECIBIR se necesita todo el catálogo (llega lo que no había); para
    TRASPASAR, solo lo que el origen tiene (`solo_con_existencia`).
    """
    _exigir_ver(actor)
    condicion = "AND COALESCE(e.cantidad, 0) > 0" if solo_con_existencia else ""
    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_base,
                       p.maneja_lote, COALESCE(e.cantidad, 0) AS existencia,
                       pres.presentaciones
                  FROM productos p
                  LEFT JOIN existencias e ON e.producto_id = p.id AND e.almacen_id = :a
                  {SQL_PRESENTACIONES}
                 WHERE p.activo {condicion}
                 ORDER BY p.nombre
                 LIMIT 2000
                """  # noqa: S608 — fragmentos constantes del código
            ),
            {"a": almacen_id},
        )
    ).mappings().all()
    return [
        ProductoParaCapturar(
            **{k: f[k] for k in ("producto_id", "sku", "nombre", "unidad_base",
                                  "maneja_lote", "existencia")},
            presentaciones=_presentaciones(f["presentaciones"]),
        )
        for f in filas
    ]


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------
@router.get("/entradas", response_model=Entradas)
async def entradas(actor: ActorDep, sesion: SesionDep) -> Entradas:
    _exigir_ver(actor)
    return Entradas(
        entradas=[
            EntradaEnLista(
                **{k: e[k] for k in ("id", "folio", "motivo", "estado", "proveedor",
                                      "referencia", "fecha_operativa", "bodega",
                                      "renglones", "piezas", "importe_total")},
                motivo_etiqueta=etiqueta(e["motivo"]),
            )
            for e in await entradas_recientes(sesion)
        ],
        bodegas=[Opcion(**dict(b)) for b in await _bodegas(sesion)],
        proveedores=[Opcion(id=p["id"], codigo=p["codigo"], nombre=p["nombre"])
                     for p in await _proveedores(sesion)],
        motivos=[
            MotivoDeEntrada(clave=clave, etiqueta=v[1], explicacion=v[2])
            for clave, v in MOTIVOS.items()
        ],
    )


@router.post("/entradas", response_model=Entrada)
async def abrir(peticion: PeticionEntrada, actor: ActorDep, sesion: SesionDep) -> Entrada:
    actor.exigir(PERMISO_AJUSTAR)
    try:
        entrada_id = await abrir_entrada(
            sesion,
            almacen_destino_id=str(peticion.almacen_destino_id),
            motivo=peticion.motivo,
            proveedor_id=str(peticion.proveedor_id) if peticion.proveedor_id else "",
            proveedor=peticion.proveedor,
            referencia=peticion.referencia,
            nota=peticion.nota,
            quien=actor.usuario_id,
        )
    except EntradaRechazada as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _entrada(sesion, entrada_id, mensaje="Entrada abierta: agrega lo que llegó.")


@router.post("/compras", response_model=Entrada)
async def recibir_compra(peticion: PeticionCompra, actor: ActorDep, sesion: SesionDep) -> Entrada:
    """La compra que el gerente recibió en la calle, quizá capturada sin señal (§83).

    De un jalón: abre la entrada con el id del teléfono, agrega los renglones y la
    confirma en la bodega principal. **Idempotente**: si ese id ya entró, devuelve
    la misma entrada sin volver a sumar; si quedó en borrador por un intento que
    se cortó a la mitad, se rehace. Así el teléfono puede reintentar sin miedo
    cada vez que recupera la señal.
    """
    actor.exigir(PERMISO_AJUSTAR)
    existente = (
        await sesion.execute(
            text("SELECT estado, folio FROM entradas WHERE id = :id"), {"id": peticion.id}
        )
    ).mappings().first()
    if existente is not None and existente["estado"] == "confirmada":
        return await _entrada(
            sesion,
            peticion.id,
            mensaje=f"Esta compra ya se había recibido ({existente['folio']}): no se "
            "sumó otra vez.",
        )
    if existente is not None and existente["estado"] != "borrador":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La entrada {existente['folio']} de esta compra está {existente['estado']}.",
        )

    bodega = await bodega_principal(sesion)
    if bodega is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay ninguna bodega activa.")

    try:
        if existente is None:
            await abrir_entrada(
                sesion,
                almacen_destino_id=str(bodega["id"]),
                motivo="compra",
                proveedor_id=str(peticion.proveedor_id) if peticion.proveedor_id else "",
                proveedor=peticion.proveedor,
                referencia=peticion.referencia,
                fecha=peticion.fecha,
                nota=peticion.nota,
                quien=actor.usuario_id,
                entrada_id=peticion.id,
                costo_opcional=True,
            )
        else:
            await sesion.execute(
                text("DELETE FROM entrada_detalle WHERE entrada_id = :e"), {"e": peticion.id}
            )
            await sesion.commit()
        for r in peticion.renglones:
            sku = (
                await sesion.execute(
                    text("SELECT sku FROM productos WHERE id = :p"), {"p": r.producto_id}
                )
            ).scalar_one_or_none()
            if sku is None:
                raise EntradaRechazada(
                    "Un producto de la compra ya no existe en el catálogo. Quítalo y "
                    "vuelve a mandarla."
                )
            await agregar_renglon_a_entrada(
                sesion,
                peticion.id,
                producto=sku,
                unidad_codigo=r.unidad,
                cantidad=r.cantidad,
                costo=r.costo,
                lote="",
                caducidad="",
            )
        aviso = await confirmar_entrada(sesion, peticion.id, quien=actor.usuario_id)
    except EntradaNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (EntradaRechazada, CapturaInvalida, CantidadInvalida) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _entrada(sesion, peticion.id, mensaje=aviso)


@router.get("/entradas/{entrada_id}", response_model=Entrada)
async def ver_entrada(entrada_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Entrada:
    _exigir_ver(actor)
    return await _entrada(sesion, entrada_id)


@router.post("/entradas/{entrada_id}/renglones", response_model=Entrada)
async def agregar(
    entrada_id: uuid.UUID, peticion: PeticionRenglonDeEntrada, actor: ActorDep,
    sesion: SesionDep,
) -> Entrada:
    actor.exigir(PERMISO_AJUSTAR)
    try:
        aviso = await agregar_renglon_a_entrada(
            sesion,
            entrada_id,
            producto=peticion.sku,
            unidad_codigo=peticion.unidad,
            cantidad=peticion.cantidad,
            costo=peticion.costo,
            lote=peticion.lote,
            caducidad=peticion.caducidad,
        )
    except EntradaNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (EntradaRechazada, CapturaInvalida, CantidadInvalida) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _entrada(sesion, entrada_id, mensaje=aviso)


@router.post("/entradas/{entrada_id}/renglones/{renglon_id}/quitar", response_model=Entrada)
async def quitar(
    entrada_id: uuid.UUID, renglon_id: uuid.UUID, actor: ActorDep, sesion: SesionDep
) -> Entrada:
    actor.exigir(PERMISO_AJUSTAR)
    try:
        await quitar_renglon_de_entrada(sesion, entrada_id, renglon_id)
    except EntradaNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except EntradaRechazada as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _entrada(sesion, entrada_id, mensaje="Renglón quitado.")


@router.post("/entradas/{entrada_id}/confirmar", response_model=Entrada)
async def confirmar(entrada_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Entrada:
    """Mete la mercancía a la bodega: libro mayor, costo y cuenta por pagar.
    Confirmar dos veces no la mete dos veces (el segundo recibe 409)."""
    actor.exigir(PERMISO_AJUSTAR)
    try:
        aviso = await confirmar_entrada(sesion, entrada_id, quien=actor.usuario_id)
    except EntradaNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except EntradaRechazada as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _entrada(sesion, entrada_id, mensaje=aviso)


@router.post("/entradas/{entrada_id}/cancelar", response_model=Entrada)
async def cancelar(
    entrada_id: uuid.UUID, peticion: PeticionCancelar, actor: ActorDep, sesion: SesionDep
) -> Entrada:
    actor.exigir(PERMISO_AJUSTAR)
    try:
        await cancelar_entrada(
            sesion, entrada_id, motivo=peticion.motivo, quien=actor.usuario_id
        )
    except EntradaNoExiste as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except EntradaRechazada as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return await _entrada(sesion, entrada_id, mensaje="Entrada cancelada.")


async def _entrada(sesion, entrada_id: uuid.UUID, *, mensaje: str | None = None) -> Entrada:
    datos = await datos_de_la_entrada(sesion, entrada_id)
    if datos is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa entrada no existe")
    e = datos["entrada"]
    cuenta = datos["cuenta"]
    return Entrada(
        id=e["id"],
        folio=e["folio"],
        motivo=e["motivo"],
        motivo_etiqueta=etiqueta(e["motivo"]),
        estado=e["estado"],
        editable=e["estado"] == "borrador",
        exige_costo=e["motivo"] == "compra",
        proveedor=e["proveedor"],
        referencia=e["referencia"],
        nota=e["nota"],
        fecha_operativa=e["fecha_operativa"],
        bodega=e["bodega_nombre"],
        almacen_destino_id=e["almacen_destino_id"],
        renglones=[
            RenglonDeEntrada(
                **{k: r[k] for k in ("id", "sku", "nombre", "unidad_base", "cantidad",
                                      "unidad_codigo", "unidades_capturadas",
                                      "costo_unitario", "importe", "lote", "existencia")},
                proyectado=datos["proyectado"][r["producto_id"]],
            )
            for r in datos["renglones"]
        ],
        total_piezas=datos["total_piezas"],
        importe_capturado=datos["importe_capturado"],
        sin_costo=len(datos["sin_costo"]),
        cuenta=CuentaPorPagar(**{k: cuenta[k] for k in CuentaPorPagar.model_fields})
        if cuenta
        else None,
        mensaje=mensaje,
    )


# ---------------------------------------------------------------------------
# Traspasos entre bodegas
# ---------------------------------------------------------------------------
@router.get("/traspasos", response_model=Traspasos)
async def traspasos(actor: ActorDep, sesion: SesionDep) -> Traspasos:
    _exigir_ver(actor)
    return Traspasos(
        traspasos=[TraspasoEnLista(**dict(t)) for t in await traspasos_entre_bodegas(sesion)],
        bodegas=[Opcion(**dict(b)) for b in await _bodegas(sesion)],
    )


@router.post("/traspasos", response_model=TraspasoHecho)
async def traspasar(
    peticion: PeticionTraspaso, actor: ActorDep, sesion: SesionDep
) -> TraspasoHecho:
    """De una bodega a otra, en un paso. No deja el origen en negativo."""
    actor.exigir(PERMISO_AJUSTAR)
    try:
        hecho = await traspasar_entre_bodegas(
            sesion,
            origen_id=peticion.origen_id,
            destino_id=peticion.destino_id,
            pedidos=[
                PedidoDeTraspaso(producto_id=r.producto_id, unidad=r.unidad, cantidad=r.cantidad)
                for r in peticion.renglones
            ],
            nota=peticion.nota,
            quien=actor.usuario_id,
        )
    except TraspasoRechazado as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return TraspasoHecho(id=hecho.id, folio=hecho.folio, mensaje=hecho.mensaje)
