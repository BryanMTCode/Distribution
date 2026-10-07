"""Los clientes, desde el teléfono de la oficina (`/v1/oficina/clientes`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE, Y POR QUÉ NO ES `/v1/clientes`
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «agrega lo de los clientes en la app del
gerente». `/v1/clientes` es otra cosa: la cartera de la RUTA del vendedor, que su
teléfono baja para vender sin señal. Esto es la vista de la oficina sobre TODOS
los clientes: quién debe, desde cuándo, qué compra, y bloquear o desbloquear su
crédito.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
Ver: `ventas.ver_todas` (admin, gerente, supervisor), el mismo permiso que la
pantalla de Vendedores: la cartera y las compras de todas las rutas no son del
vendedor. Bloquear el crédito: `clientes.administrar`, el mismo que en el panel.

Las cifras de crédito salen de `v_cartera_cliente`, la misma vista que el panel y
que el teléfono del vendedor al decidir si le fía.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Dinero

router = APIRouter(prefix="/oficina/clientes", tags=["oficina"])

PERMISO_VER = "ventas.ver_todas"
PERMISO_ADMINISTRAR = "clientes.administrar"

# Los filtros de la lista. «con_saldo» y «vencidos» son las preguntas de cobranza;
# «bloqueados» y «prospectos», las de la oficina.
FILTROS = {
    "todos": "c.estatus NOT IN ('inactivo', 'baja')",
    "con_saldo": "COALESCE(cart.saldo, 0) > 0",
    "vencidos": "COALESCE(cart.saldo_vencido, 0) > 0",
    "bloqueados": "c.bloqueado",
    "prospectos": "c.estatus = 'prospecto'",
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
    bloqueado: bool
    telefono: str | None
    saldo: Dinero
    saldo_vencido: Dinero
    facturas_vencidas: int
    ultima_compra: date | None


class ListaDeClientes(BaseModel):
    filtro: str
    clientes: list[ClienteEnLista]
    recortado: bool
    conteos: dict[str, int]


class CuentaAbierta(BaseModel):
    venta_id: uuid.UUID
    folio: str | None
    fecha_emision: date
    fecha_vencimiento: date
    importe_original: Dinero
    importe_pagado: Dinero
    saldo: Dinero
    vencida: bool
    dias_vencida: int


class VentaDelCliente(BaseModel):
    id: uuid.UUID
    folio: str | None
    fecha: date
    momento: datetime | None
    tipo: str
    estado: str
    total: Dinero
    vendedor: str


class CobroDelCliente(BaseModel):
    folio: str | None
    fecha: date
    importe: Dinero
    forma_pago: str
    estado: str
    vendedor: str


class FichaDelCliente(BaseModel):
    id: uuid.UUID
    codigo: str | None
    nombre: str
    razon_social: str | None
    contacto: str | None
    telefono: str | None
    direccion: str | None
    ruta: str | None
    estatus: str
    # Crédito
    permite_credito: bool
    limite_credito: Dinero
    dias_credito: int | None
    bloqueado: bool
    bloqueo_motivo: str | None
    saldo: Dinero
    disponible: Dinero
    saldo_vencido: Dinero
    por_confirmar: Dinero
    # Lo que compra
    comprado_mes: Dinero
    comprado_anio: Dinero
    cuentas: list[CuentaAbierta]
    ventas: list[VentaDelCliente]
    cobros: list[CobroDelCliente]
    puede_bloquear: bool
    mensaje: str | None = None


class PeticionBloqueo(BaseModel):
    bloquear: bool
    motivo: str = Field(default="", max_length=300)


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
                       c.bloqueado, c.telefono, r.nombre AS ruta,
                       COALESCE(cart.saldo, 0) AS saldo,
                       COALESCE(cart.saldo_vencido, 0) AS saldo_vencido,
                       COALESCE(cart.facturas_vencidas, 0) AS facturas_vencidas,
                       (SELECT max(v.fecha_operativa) FROM ventas v
                         WHERE v.cliente_id = c.id AND v.estado = 'confirmada')
                         AS ultima_compra
                  FROM clientes c
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                  LEFT JOIN v_cartera_cliente cart ON cart.cliente_id = c.id
                 WHERE {" AND ".join(condiciones)}
                 -- Lo que más urge cobrar primero; luego por nombre.
                 ORDER BY COALESCE(cart.saldo_vencido, 0) DESC,
                          COALESCE(cart.saldo, 0) DESC,
                          c.nombre_comercial
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
                  LEFT JOIN v_cartera_cliente cart ON cart.cliente_id = c.id
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
    return await _ficha(sesion, actor, cliente_id)


@router.post("/{cliente_id}/bloqueo", response_model=FichaDelCliente)
async def bloqueo(
    cliente_id: uuid.UUID, peticion: PeticionBloqueo, actor: ActorDep, sesion: SesionDep
) -> FichaDelCliente:
    """Bloquea o desbloquea la venta a CRÉDITO, como en el panel.

    El bloqueo pide motivo —el vendedor va a preguntar por qué no le puede fiar a
    esa tienda, y el motivo le llega en el delta del cliente— y no impide la venta
    de contado.
    """
    actor.exigir(PERMISO_ADMINISTRAR)
    motivo = peticion.motivo.strip()
    if peticion.bloquear and not motivo:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Escribe por qué se bloquea. El vendedor va a preguntar.",
        )
    resultado = await sesion.execute(
        text(
            "UPDATE clientes SET bloqueado = :b, bloqueo_motivo = :m, "
            "       actualizado_en = now() WHERE id = :id"
        ),
        {"id": cliente_id, "b": peticion.bloquear, "m": motivo if peticion.bloquear else None},
    )
    if resultado.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese cliente no existe")
    await sesion.commit()
    return await _ficha(
        sesion,
        actor,
        cliente_id,
        mensaje="Bloqueado para crédito: puede seguir comprando de contado."
        if peticion.bloquear
        else "Desbloqueado: ya se le puede vender a crédito.",
    )


async def _ficha(sesion, actor, cliente_id: uuid.UUID, *, mensaje: str | None = None):
    c = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.codigo, c.nombre_comercial AS nombre, c.razon_social,
                       c.contacto_nombre AS contacto, c.telefono, c.estatus,
                       concat_ws(', ', NULLIF(concat_ws(' ', c.calle, c.numero), ''),
                                 c.colonia, c.municipio) AS direccion,
                       c.permite_credito, c.limite_credito, c.dias_credito,
                       c.bloqueado, c.bloqueo_motivo,
                       r.nombre AS ruta,
                       COALESCE(cart.saldo, 0) AS saldo,
                       COALESCE(cart.disponible, 0) AS disponible,
                       COALESCE(cart.saldo_vencido, 0) AS saldo_vencido,
                       COALESCE(cart.por_confirmar, 0) AS por_confirmar,
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
                  LEFT JOIN v_cartera_cliente cart ON cart.cliente_id = c.id
                 WHERE c.id = :id
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().first()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ese cliente no existe")

    cuentas = (
        await sesion.execute(
            text(
                """
                SELECT x.venta_id, v.folio_local AS folio, x.fecha_emision,
                       x.fecha_vencimiento, x.importe_original, x.importe_pagado,
                       x.saldo,
                       x.fecha_vencimiento < CURRENT_DATE AS vencida,
                       GREATEST(CURRENT_DATE - x.fecha_vencimiento, 0) AS dias_vencida
                  FROM cuentas_por_cobrar x
                  JOIN ventas v ON v.id = x.venta_id
                 WHERE x.cliente_id = :id AND x.estado IN ('abierta', 'parcial')
                 ORDER BY x.fecha_vencimiento
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().all()
    ventas = (
        await sesion.execute(
            text(
                """
                SELECT v.id, v.folio_local AS folio, v.fecha_operativa AS fecha,
                       v.fecha_dispositivo AS momento, v.tipo, v.estado, v.total,
                       u.nombre AS vendedor
                  FROM ventas v JOIN usuarios u ON u.id = v.vendedor_id
                 WHERE v.cliente_id = :id
                 ORDER BY v.fecha_operativa DESC, v.fecha_dispositivo DESC
                 LIMIT 30
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().all()
    cobros = (
        await sesion.execute(
            text(
                """
                SELECT k.folio_local AS folio, k.fecha_operativa AS fecha, k.importe,
                       k.forma_pago, k.estado, u.nombre AS vendedor
                  FROM cobros k JOIN usuarios u ON u.id = k.vendedor_id
                 WHERE k.cliente_id = :id
                 ORDER BY k.fecha_operativa DESC, k.fecha_dispositivo DESC
                 LIMIT 30
                """
            ),
            {"id": cliente_id},
        )
    ).mappings().all()

    datos = dict(c)
    datos["limite_credito"] = datos["limite_credito"] or Decimal(0)
    datos["direccion"] = datos["direccion"] or None
    return FichaDelCliente(
        **datos,
        cuentas=[CuentaAbierta(**dict(x)) for x in cuentas],
        ventas=[VentaDelCliente(**dict(v)) for v in ventas],
        cobros=[CobroDelCliente(**dict(k)) for k in cobros],
        puede_bloquear=actor.puede(PERMISO_ADMINISTRAR),
        mensaje=mensaje,
    )
