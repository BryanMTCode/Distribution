"""Los clientes, desde el teléfono de la oficina (`/v1/oficina/clientes`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE, Y POR QUÉ NO ES `/v1/clientes`
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «agrega lo de los clientes en la app del
gerente». `/v1/clientes` es otra cosa: los clientes de la RUTA del vendedor. Esto
es la vista de la oficina sobre TODOS los clientes: dónde están, qué compran y
cómo pagan.

La operación es de contado (ADR 0002 §81): aquí ya no hay saldo, cartera vencida
ni bloqueo de crédito.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
Ver: `ventas.ver_todas` (admin, gerente, supervisor), el mismo permiso que la
pantalla de Vendedores: las compras de todas las rutas no son del vendedor.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette import status

from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Dinero
from app.infra.ubicacion_cliente import UbicacionInvalida, fijar_ubicacion, leer_coordenadas

router = APIRouter(prefix="/oficina/clientes", tags=["oficina"])

PERMISO_VER = "ventas.ver_todas"
PERMISO_UBICAR = "clientes.ubicar"

# Los filtros de la lista: las preguntas de la oficina.
FILTROS = {
    "todos": "c.estatus NOT IN ('inactivo', 'baja')",
    "prospectos": "c.estatus = 'prospecto'",
    # Sin coordenadas el vendedor no tiene geocerca y el mapa no lo dibuja.
    "sin_ubicacion": "c.estatus NOT IN ('inactivo', 'baja') AND (c.lat IS NULL OR c.lng IS NULL)",
}


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class ClienteEnLista(BaseModel):
    id: uuid.UUID
    codigo: str | None
    nombre: str
    ruta: str | None
    estatus: str
    telefono: str | None
    con_ubicacion: bool
    ultima_compra: date | None


class ListaDeClientes(BaseModel):
    filtro: str
    clientes: list[ClienteEnLista]
    recortado: bool
    conteos: dict[str, int]


class VentaDelCliente(BaseModel):
    id: uuid.UUID
    folio: str | None
    fecha: date
    momento: datetime | None
    tipo: str
    # 'efectivo' o 'transferencia'; nula solo en las ventas a crédito del piloto.
    forma_pago: str | None
    estado: str
    total: Dinero
    vendedor: str


class FichaDelCliente(BaseModel):
    id: uuid.UUID
    codigo: str | None
    nombre: str
    razon_social: str | None
    contacto: str | None
    telefono: str | None
    direccion: str | None
    referencias: str | None
    ruta: str | None
    estatus: str
    # Dónde está: lo que usa la geocerca de la venta y el mapa del tablero.
    lat: Decimal | None
    lng: Decimal | None
    ubicacion_origen: str | None
    ubicacion_capturada_en: datetime | None
    # Lo que compra
    comprado_mes: Dinero
    comprado_anio: Dinero
    ventas: list[VentaDelCliente]
    # Si quien la ve puede fijar o corregir la ubicación (`clientes.ubicar`).
    puede_ubicar: bool = False
    mensaje: str | None = None


class PeticionUbicacion(BaseModel):
    # Texto: se valida con la regla de los tres caminos (`ubicacion_cliente.py`).
    lat: str = Field(max_length=24)
    lng: str = Field(max_length=24)
    # 'gps' si se tomó parado en el negocio; 'manual' si se escribió o corrigió.
    origen: str = Field(default="manual", max_length=10)
    precision_m: str | None = Field(default=None, max_length=12)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@router.get("", response_model=ListaDeClientes)
async def lista(
    actor: ActorDep, sesion: SesionDep, q: str = "", filtro: str = "todos"
) -> ListaDeClientes:
    actor.exigir(PERMISO_VER)
    if filtro not in FILTROS:
        filtro = "todos"
    condiciones = [FILTROS[filtro]]
    parametros: dict[str, object] = {"limite": 301}
    busqueda = q.strip()
    if busqueda:
        condiciones.append(
            "(c.nombre_comercial ILIKE :q OR c.codigo ILIKE :q OR c.telefono ILIKE :q)"
        )
        parametros["q"] = f"%{busqueda}%"

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT c.id, c.codigo, c.nombre_comercial AS nombre, c.estatus,
                       c.telefono, r.nombre AS ruta,
                       (c.lat IS NOT NULL AND c.lng IS NOT NULL) AS con_ubicacion,
                       (SELECT max(v.fecha_operativa) FROM ventas v
                         WHERE v.cliente_id = c.id AND v.estado = 'confirmada')
                         AS ultima_compra
                  FROM clientes c
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                 WHERE {" AND ".join(condiciones)}
                 ORDER BY c.nombre_comercial
                 LIMIT :limite
                """  # noqa: S608 — las condiciones son constantes del código
            ),
            parametros,
        )
    ).mappings().all()

    conteos = (
        await sesion.execute(
            text(
                f"""
                SELECT {", ".join(
                    f"count(*) FILTER (WHERE {condicion}) AS {clave}"
                    for clave, condicion in FILTROS.items()
                )}
                  FROM clientes c
                """  # noqa: S608 — constantes del código
            )
        )
    ).mappings().one()

    return ListaDeClientes(
        filtro=filtro,
        clientes=[ClienteEnLista(**dict(f)) for f in filas[:300]],
        recortado=len(filas) > 300,
        conteos=dict(conteos),
    )


@router.get("/{cliente_id}", response_model=FichaDelCliente)
async def ficha(cliente_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> FichaDelCliente:
    actor.exigir(PERMISO_VER)
    return await ficha_del_cliente(
        sesion, cliente_id, puede_ubicar=actor.puede(PERMISO_UBICAR)
    )


@router.post("/{cliente_id}/ubicacion", response_model=FichaDelCliente)
async def ubicar(
    cliente_id: uuid.UUID, peticion: PeticionUbicacion, actor: ActorDep, sesion: SesionDep
) -> FichaDelCliente:
    """Fija o corrige la ubicación: la del GPS del teléfono o la escrita a mano (§84)."""
    actor.exigir(PERMISO_VER)
    actor.exigir(PERMISO_UBICAR)
    try:
        lat, lng = leer_coordenadas(peticion.lat, peticion.lng)
        precision = None
        if peticion.origen == "gps" and peticion.precision_m:
            precision = Decimal(peticion.precision_m)
        await fijar_ubicacion(
            sesion, cliente_id, lat=lat, lng=lng, origen=peticion.origen,
            precision_m=precision, quien=actor.usuario_id,
        )
    except UbicacionInvalida as e:
        codigo = status.HTTP_404_NOT_FOUND if "no existe" in str(e) else status.HTTP_409_CONFLICT
        raise HTTPException(codigo, str(e)) from e
    except ArithmeticError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "La precisión no es un número.") from e
    await sesion.commit()
    return await ficha_del_cliente(
        sesion,
        cliente_id,
        mensaje="Ubicación guardada. Los teléfonos de su ruta la reciben al sincronizar.",
        puede_ubicar=True,
    )


async def ficha_del_cliente(
    sesion, cliente_id: uuid.UUID, *, mensaje: str | None = None, puede_ubicar: bool = False
) -> FichaDelCliente:
    c = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.codigo, c.nombre_comercial AS nombre, c.razon_social,
                       c.contacto_nombre AS contacto, c.telefono, c.estatus,
                       concat_ws(', ', NULLIF(concat_ws(' ', c.calle, c.numero), ''),
                                 c.colonia, c.municipio) AS direccion,
                       c.referencias, c.lat, c.lng, c.ubicacion_origen,
                       c.ubicacion_capturada_en,
                       r.nombre AS ruta,
                       (SELECT COALESCE(sum(total), 0) FROM ventas v
                         WHERE v.cliente_id = c.id AND v.estado = 'confirmada'
                           AND v.fecha_operativa >= date_trunc('month', CURRENT_DATE))
                         AS comprado_mes,
                       (SELECT COALESCE(sum(total), 0) FROM ventas v
                         WHERE v.cliente_id = c.id AND v.estado = 'confirmada'
                           AND v.fecha_operativa >= date_trunc('year', CURRENT_DATE))
                         AS comprado_anio
                  FROM clientes c
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                 WHERE c.id = :id
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().first()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese cliente no existe")

    ventas = (
        await sesion.execute(
            text(
                """
                SELECT v.id, v.folio_local AS folio, v.fecha_operativa AS fecha,
                       v.fecha_dispositivo AS momento, v.tipo, v.forma_pago, v.estado,
                       v.total, u.nombre AS vendedor
                  FROM ventas v JOIN usuarios u ON u.id = v.vendedor_id
                 WHERE v.cliente_id = :id
                 ORDER BY v.fecha_operativa DESC, v.fecha_dispositivo DESC
                 LIMIT 30
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().all()

    datos = dict(c)
    datos["direccion"] = datos["direccion"] or None
    return FichaDelCliente(
        **datos,
        ventas=[VentaDelCliente(**dict(v)) for v in ventas],
        puede_ubicar=puede_ubicar,
        mensaje=mensaje,
    )
