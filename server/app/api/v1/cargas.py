"""Cargar el camión desde la app (`/v1/cargas`).

────────────────────────────────────────────────────────────────────────────
POR QUÉ EXISTE
────────────────────────────────────────────────────────────────────────────
Pedido en operación (octubre 2026): «las cargas quiero hacerlas también en la
app, pero solo para los administradores y puestos de arriba, no para los
vendedores». A las seis de la mañana quien carga está en la bodega con el
teléfono en la mano, no frente a la computadora del panel.

────────────────────────────────────────────────────────────────────────────
LAS MISMAS REGLAS QUE EL PANEL, NO UNA COPIA DE ELLAS
────────────────────────────────────────────────────────────────────────────
Todo lo que decide algo se importa de `app/api/admin/cargas.py`:

· `mover_y_confirmar`: libro mayor, caché de existencias y delta al teléfono
  del vendedor, en una sola transacción.
· `_bloqueos_para_cargar`: la regla del §2.3 (no se carga encima de un día que
  no ha subido o no se ha liquidado), que se puede forzar con un motivo que
  queda en `auditoria`.
· `_leer_bultos` y `cantidad_base`: bultos enteros, convertidos a unidad base
  una sola vez, al capturar.

Este archivo solo traduce de JSON a esas funciones. Si mañana cambia una regla
de la carga, cambia para los dos caminos a la vez.

────────────────────────────────────────────────────────────────────────────
QUIÉN
────────────────────────────────────────────────────────────────────────────
Todo exige `inventario.cargar`: admin, supervisor y —desde la migración 0044—
gerente. El vendedor no lo tiene ni lo tendrá: cargarse su propio camión sería
firmar su propia entrega. La app esconde el botón; el servidor lo prohíbe.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.admin.cargas import (
    EDITABLE,
    PERMISO,
    _bloqueos_para_cargar,
    _bodegas,
    _leer_bultos,
    _lo_que_hay_en_la_bodega,
    _vendedores,
    mover_y_confirmar,
)
from app.api.admin.comun import CapturaInvalida, sin_decimales, texto_o_nulo
from app.api.deps import ActorDep, SesionDep
from app.api.esquemas import Cantidad
from app.domain.importes import CantidadInvalida, cantidad_base

router = APIRouter(prefix="/cargas", tags=["cargas"])


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class Opcion(BaseModel):
    id: uuid.UUID
    codigo: str
    nombre: str
    camion: str | None = None


class Opciones(BaseModel):
    """Lo que hace falta para abrir una carga: a quién y de qué bodega."""

    vendedores: list[Opcion]
    bodegas: list[Opcion]
    hoy: date
    # Para cuándo se abre la carga si no se dice: el día siguiente. Solo hay
    # una carga al día y se hace la víspera (ADR 0002 §82).
    manana: date


class CargaResumen(BaseModel):
    id: uuid.UUID
    folio: str
    estado: str
    fecha_operativa: date
    vendedor: str
    camion: str
    renglones: int
    piezas: Cantidad


class Presentacion(BaseModel):
    unidad: str
    factor: Cantidad


class Renglon(BaseModel):
    id: uuid.UUID
    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    cantidad: Cantidad
    en_bodega: Cantidad


class EnBodega(BaseModel):
    """Un producto que se puede subir: tiene existencia en la bodega de origen."""

    producto_id: uuid.UUID
    sku: str
    nombre: str
    unidad_base: str
    en_bodega: Cantidad
    ya_en_la_carga: Cantidad
    presentaciones: list[Presentacion]
    # La más grande: en la bodega se carga por caja (ver `_lo_que_hay_en_la_bodega`).
    por_omision: str | None


class Carga(BaseModel):
    id: uuid.UUID
    folio: str
    estado: str
    fecha_operativa: date
    vendedor: str
    camion: str
    bodega: str
    editable: bool
    renglones: list[Renglon]
    surtido: list[EnBodega]
    # Lo que impediría confirmar ahora mismo. Se ve mientras se captura:
    # enterarse al final, después de quince renglones, es perder el trabajo.
    bloqueos: list[str]
    mensaje: str | None = None


class PeticionNueva(BaseModel):
    vendedor_id: uuid.UUID
    almacen_origen_id: uuid.UUID
    fecha_operativa: date | None = None


class PedidoDeRenglon(BaseModel):
    producto_id: uuid.UUID
    unidad: str = Field(max_length=20)
    # Texto y no número: se valida con `_leer_bultos`, la misma que el panel, y
    # así «2.5» o «1e3» se rechazan con el mismo mensaje en los dos caminos.
    cantidad: str = Field(max_length=20)


class PeticionRenglones(BaseModel):
    renglones: list[PedidoDeRenglon] = Field(min_length=1, max_length=500)


class PeticionConfirmar(BaseModel):
    # Solo hace falta si hay bloqueos: confirmar encima de ellos exige decir por
    # qué, y eso queda en `auditoria` con la lista de lo que se pasó por alto.
    motivo_forzado: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
@router.get("/opciones", response_model=Opciones)
async def opciones(actor: ActorDep, sesion: SesionDep) -> Opciones:
    actor.exigir(PERMISO)
    return Opciones(
        vendedores=[Opcion(**dict(v)) for v in await _vendedores(sesion)],
        bodegas=[Opcion(**dict(b)) for b in await _bodegas(sesion)],
        hoy=date.today(),
        manana=date.today() + timedelta(days=1),
    )


@router.get("", response_model=list[CargaResumen])
async def abiertas(actor: ActorDep, sesion: SesionDep) -> list[CargaResumen]:
    """Las que siguen vivas: en captura, o cargadas y sin liquidar.

    Las de días pasados también, mientras no se liquiden: son justo las que
    bloquean la carga de hoy, y quien la intenta tiene que poder verlas.
    """
    actor.exigir(PERMISO)
    filas = (
        await sesion.execute(
            text(
                """
                SELECT c.id, c.folio, c.estado, c.fecha_operativa,
                       u.nombre AS vendedor, a.nombre AS camion,
                       COALESCE(d.renglones, 0) AS renglones,
                       COALESCE(d.piezas, 0) AS piezas
                  FROM cargas c
                  JOIN usuarios u ON u.id = c.vendedor_id
                  JOIN almacenes a ON a.id = c.almacen_destino_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) AS renglones, sum(cantidad) AS piezas
                          FROM carga_detalle WHERE carga_id = c.id
                  ) d ON true
                 WHERE c.estado IN ('borrador', 'confirmada', 'en_ruta')
                 ORDER BY c.fecha_operativa DESC, c.folio DESC
                 LIMIT 100
                """
            )
        )
    ).mappings().all()
    return [CargaResumen(**dict(f)) for f in filas]


@router.get("/{carga_id}", response_model=Carga)
async def ver(carga_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Carga:
    actor.exigir(PERMISO)
    return await _carga(sesion, carga_id)


async def _carga(sesion, carga_id: uuid.UUID, *, mensaje: str | None = None) -> Carga:
    carga = (
        await sesion.execute(
            text(
                "SELECT c.id, c.folio, c.estado, c.fecha_operativa, c.vendedor_id, "
                "       c.almacen_origen_id, u.nombre AS vendedor, "
                "       a.nombre AS camion, o.nombre AS bodega "
                "  FROM cargas c "
                "  JOIN usuarios u ON u.id = c.vendedor_id "
                "  JOIN almacenes a ON a.id = c.almacen_destino_id "
                "  JOIN almacenes o ON o.id = c.almacen_origen_id "
                " WHERE c.id = :id"
            ),
            {"id": carga_id},
        )
    ).mappings().first()
    if carga is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa carga no existe")

    renglones = (
        await sesion.execute(
            text(
                """
                SELECT d.id, d.producto_id, d.cantidad, p.sku, p.nombre, p.unidad_base,
                       COALESCE(e.cantidad, 0) AS en_bodega
                  FROM carga_detalle d
                  JOIN productos p ON p.id = d.producto_id
                  LEFT JOIN existencias e ON e.producto_id = d.producto_id
                       AND e.almacen_id = :bodega
                 WHERE d.carga_id = :id
                 ORDER BY p.nombre
                """
            ),
            {"id": carga_id, "bodega": carga["almacen_origen_id"]},
        )
    ).mappings().all()

    editable = carga["estado"] in EDITABLE
    surtido = (
        await _lo_que_hay_en_la_bodega(sesion, carga_id, carga["almacen_origen_id"], "")
        if editable
        else []
    )
    bloqueos = (
        await _bloqueos_para_cargar(sesion, carga["vendedor_id"], carga["fecha_operativa"])
        if editable
        else []
    )

    return Carga(
        id=carga["id"],
        folio=carga["folio"],
        estado=carga["estado"],
        fecha_operativa=carga["fecha_operativa"],
        vendedor=carga["vendedor"],
        camion=carga["camion"],
        bodega=carga["bodega"],
        editable=editable,
        renglones=[Renglon(**dict(r)) for r in renglones],
        surtido=[
            EnBodega(
                producto_id=s["id"],
                sku=s["sku"],
                nombre=s["nombre"],
                unidad_base=s["unidad_base"],
                en_bodega=s["en_bodega"],
                ya_en_la_carga=s["ya_en_la_carga"],
                presentaciones=[
                    Presentacion(unidad=p["unidad"], factor=Decimal(p["factor"]))
                    for p in (s["presentaciones"] or [])
                ],
                por_omision=s["por_omision"],
            )
            for s in surtido
        ],
        bloqueos=bloqueos,
        mensaje=mensaje,
    )


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
@router.post("", response_model=Carga)
async def abrir(peticion: PeticionNueva, actor: ActorDep, sesion: SesionDep) -> Carga:
    """Abre la carga en borrador; si el vendedor ya trae una ese día, devuelve ésa.

    Devolver la existente en vez de un error es lo que espera quien la abre dos
    veces desde dos teléfonos: seguir capturando donde iba, no un 409.

    Sin fecha es para MAÑANA: solo hay una carga al día y se hace la víspera,
    después del corte (ADR 0002 §82).
    """
    actor.exigir(PERMISO)
    dia = peticion.fecha_operativa or date.today() + timedelta(days=1)

    fila = (
        await sesion.execute(
            text(
                "SELECT u.almacen_id, u.nombre, a.tipo "
                "  FROM usuarios u LEFT JOIN almacenes a ON a.id = u.almacen_id "
                " WHERE u.id = :v AND u.activo"
            ),
            {"v": peticion.vendedor_id},
        )
    ).mappings().first()
    if fila is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "ese vendedor no existe o está inactivo")
    if fila["almacen_id"] is None or fila["tipo"] != "camion":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{fila['nombre']} no tiene camión asignado. Sin almacén propio no hay "
            "a dónde cargarle.",
        )
    bodega = (
        await sesion.execute(
            text("SELECT 1 FROM almacenes WHERE id = :b AND activo AND tipo = 'bodega'"),
            {"b": peticion.almacen_origen_id},
        )
    ).scalar_one_or_none()
    if bodega is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "esa bodega no existe o está inactiva")

    abierta = (
        await sesion.execute(
            text(
                "SELECT id, folio FROM cargas "
                " WHERE vendedor_id = :v AND fecha_operativa = :d "
                "   AND estado <> 'cancelada'"
            ),
            {"v": peticion.vendedor_id, "d": dia},
        )
    ).mappings().first()
    if abierta is not None:
        return await _carga(
            sesion,
            abierta["id"],
            mensaje=f"{fila['nombre']} ya trae la carga {abierta['folio']} de ese día.",
        )

    consecutivo = (await sesion.execute(text("SELECT nextval('seq_folio_carga')"))).scalar_one()
    ruta = (
        await sesion.execute(
            text("SELECT ruta_id FROM usuarios_rutas WHERE usuario_id = :v LIMIT 1"),
            {"v": peticion.vendedor_id},
        )
    ).scalar_one_or_none()
    carga_id = uuid.uuid4()
    await sesion.execute(
        text(
            """
            INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                                vendedor_id, ruta_id, fecha_operativa, estado)
            VALUES (:id, :folio, :origen, :destino, :v, :r, :d, 'borrador')
            """
        ),
        {
            "id": carga_id,
            "folio": f"CG-{consecutivo:06d}",
            "origen": peticion.almacen_origen_id,
            "destino": fila["almacen_id"],
            "v": peticion.vendedor_id,
            "r": ruta,
            "d": dia,
        },
    )
    await sesion.commit()
    return await _carga(sesion, carga_id)


async def _exigir_borrador(sesion, carga_id: uuid.UUID, *, bloquear: bool = False) -> dict:
    fila = (
        await sesion.execute(
            text(
                "SELECT id, folio, estado, almacen_origen_id, almacen_destino_id, "
                "       vendedor_id, fecha_operativa FROM cargas WHERE id = :id"
                + (" FOR UPDATE" if bloquear else "")
            ),
            {"id": carga_id},
        )
    ).mappings().first()
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "esa carga no existe")
    if fila["estado"] not in EDITABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"esta carga ya está {fila['estado']}: lo que salió de la bodega se "
            "corrige con un traspaso o un ajuste, no editándola",
        )
    return dict(fila)


@router.post("/{carga_id}/renglones", response_model=Carga)
async def agregar(
    carga_id: uuid.UUID, peticion: PeticionRenglones, actor: ActorDep, sesion: SesionDep
) -> Carga:
    """Agrega varios renglones; si el producto ya está, se suma.

    Como en el panel: se guarda lo bueno y se nombra lo malo. Un dedazo en un
    renglón no hace perder los otros veinte.
    """
    actor.exigir(PERMISO)
    await _exigir_borrador(sesion, carga_id)

    agregados: list[str] = []
    rechazados: list[str] = []
    for pedido in peticion.renglones:
        fila = (
            await sesion.execute(
                text(
                    "SELECT p.nombre, p.unidad_base, u.factor "
                    "  FROM productos p "
                    "  LEFT JOIN producto_unidades u ON u.producto_id = p.id "
                    "       AND u.unidad_codigo = :u AND u.activo "
                    " WHERE p.id = :p AND p.activo"
                ),
                {"p": pedido.producto_id, "u": pedido.unidad},
            )
        ).mappings().first()
        if fila is None:
            rechazados.append("un producto que ya no está activo")
            continue
        if fila["factor"] is None:
            rechazados.append(f"{fila['nombre']}: no tiene la presentación {pedido.unidad}")
            continue
        try:
            cuantas = _leer_bultos(pedido.cantidad)
            en_base = cantidad_base(cuantas, Decimal(fila["factor"]))
        except (CapturaInvalida, CantidadInvalida) as e:
            rechazados.append(f"{fila['nombre']}: {str(e).rstrip('.')}")
            continue
        await sesion.execute(
            text(
                """
                INSERT INTO carga_detalle (id, carga_id, producto_id, cantidad, lote)
                VALUES (:id, :c, :p, :cant, NULL)
                ON CONFLICT (carga_id, producto_id, COALESCE(lote, ''))
                  DO UPDATE SET cantidad = carga_detalle.cantidad + excluded.cantidad
                """
            ),
            {"id": uuid.uuid4(), "c": carga_id, "p": pedido.producto_id, "cant": en_base},
        )
        agregados.append(f"{fila['nombre']} {sin_decimales(cuantas)} {pedido.unidad}")
    await sesion.commit()

    if rechazados and not agregados:
        mensaje = "No se agregó nada. " + "; ".join(rechazados) + "."
    else:
        mensaje = f"Se agregaron {len(agregados)} producto(s): " + ", ".join(agregados) + "."
        if rechazados:
            mensaje += " NO entraron: " + "; ".join(rechazados) + "."
    return await _carga(sesion, carga_id, mensaje=mensaje)


@router.post("/{carga_id}/renglones/{renglon_id}/quitar", response_model=Carga)
async def quitar(
    carga_id: uuid.UUID, renglon_id: uuid.UUID, actor: ActorDep, sesion: SesionDep
) -> Carga:
    actor.exigir(PERMISO)
    await _exigir_borrador(sesion, carga_id)
    await sesion.execute(
        text("DELETE FROM carga_detalle WHERE id = :r AND carga_id = :c"),
        {"r": renglon_id, "c": carga_id},
    )
    await sesion.commit()
    return await _carga(sesion, carga_id, mensaje="Renglón quitado.")


@router.post("/{carga_id}/confirmar", response_model=Carga)
async def confirmar(
    carga_id: uuid.UUID, peticion: PeticionConfirmar, actor: ActorDep, sesion: SesionDep
) -> Carga:
    """Mueve el inventario y manda la carga al teléfono del vendedor.

    Idempotente por estado, como en el panel: el `FOR UPDATE` y la comprobación
    de borrador hacen que un doble toque no mueva el inventario dos veces —el
    segundo recibe 409—.
    """
    actor.exigir(PERMISO)
    carga = await _exigir_borrador(sesion, carga_id, bloquear=True)

    renglones = (
        await sesion.execute(
            text(
                "SELECT producto_id, cantidad, lote, caducidad "
                "  FROM carga_detalle WHERE carga_id = :c"
            ),
            {"c": carga_id},
        )
    ).mappings().all()
    if not renglones:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "no se puede confirmar una carga sin un solo renglón")

    bloqueos = await _bloqueos_para_cargar(sesion, carga["vendedor_id"], carga["fecha_operativa"])
    razon = texto_o_nulo(peticion.motivo_forzado or "", maximo=300)
    if bloqueos and not razon:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No se puede confirmar: " + " ".join(bloqueos) + " Sincroniza el equipo y "
            "vuelve a intentar. Si el camión tiene que salir de todos modos, escribe "
            "por qué: queda registrado.",
        )

    negativos = await mover_y_confirmar(
        sesion,
        carga,
        renglones,
        quien=actor.usuario_id,
        bloqueos=bloqueos,
        razon_forzado=razon,
    )
    mensaje = (
        f"Carga {carga['folio']} confirmada: {len(renglones)} renglones. El teléfono "
        "del vendedor la recibe en su siguiente sincronización."
    )
    if negativos:
        mensaje += (
            f" Ojo: la bodega quedó con {negativos} producto(s) en negativo; es el "
            "conteo de la bodega el que hay que revisar."
        )
    return await _carga(sesion, carga_id, mensaje=mensaje)


@router.post("/{carga_id}/cancelar", response_model=Carga)
async def cancelar(carga_id: uuid.UUID, actor: ActorDep, sesion: SesionDep) -> Carga:
    """Solo un borrador. Lo confirmado ya está en el camión: se devuelve con la
    liquidación, no fingiendo que el día no pasó."""
    actor.exigir(PERMISO)
    await _exigir_borrador(sesion, carga_id, bloquear=True)
    await sesion.execute(
        text("UPDATE cargas SET estado = 'cancelada' WHERE id = :id"), {"id": carga_id}
    )
    await sesion.commit()
    return await _carga(sesion, carga_id, mensaje="Carga cancelada.")
