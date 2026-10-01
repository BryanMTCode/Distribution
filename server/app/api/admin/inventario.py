"""Existencias y el libro mayor: poder ver el inventario sin abrir una carga.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA FALTABA Y SE NOTA
────────────────────────────────────────────────────────────────────────────
Las existencias estaban visibles solo de refilón: al armar un borrador de carga,
al lado de cada renglón. Para saber qué hay en la bodega había que empezar a
cargar un camión, lo cual es absurdo y además peligroso — se abre un borrador para
consultar y alguien lo confirma.

────────────────────────────────────────────────────────────────────────────
DOS TABLAS, Y LA DIFERENCIA ENTRE ELLAS ES LA PANTALLA
────────────────────────────────────────────────────────────────────────────
`existencias` es una **caché transaccional**: un número por (almacén, producto),
actualizado en la misma transacción que el movimiento.

`movimientos_inventario` es el **libro mayor inmutable**: append-only garantizado
por disparador. Es la verdad auditable.

La caché es lo que se consulta cien veces al día; el libro mayor es lo que
responde "¿y cómo llegó a ese número?". Esta pantalla muestra la primera y deja
ver la segunda por producto, porque la pregunta que sigue a un número raro es
siempre la misma.

**Y compara las dos.** Si la suma del libro mayor no cuadra con la caché, hay un
bug en alguna transacción que escribió una y no la otra; eso no se detecta mirando
la caché, por definición. El job de reconciliación nocturno existe para esto, y
esta pantalla permite verlo sin esperar a la noche.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.sesion_web import ActorWeb

router = APIRouter(prefix="/panel/inventario", tags=["panel"], include_in_schema=False)


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen: str = "",
    q: str = "",
    filtro: str = "con_existencia",
) -> HTMLResponse:
    """Las existencias de un almacén.

    Abre en la bodega principal y mostrando **solo lo que tiene existencia**: una
    lista con los trescientos productos del catálogo en cero no responde ninguna
    pregunta operativa.
    """
    actor.exigir("inventario.ver")

    if filtro not in ("con_existencia", "negativos", "todos"):
        filtro = "con_existencia"

    almacenes = (
        await sesion.execute(
            text(
                """
                SELECT a.id, a.codigo, a.nombre, a.tipo,
                       u.nombre AS responsable,
                       COALESCE(e.renglones, 0) AS renglones,
                       COALESCE(e.piezas, 0) AS piezas,
                       COALESCE(e.negativos, 0) AS negativos
                  FROM almacenes a
                  LEFT JOIN usuarios u ON u.id = a.responsable_id
                  LEFT JOIN LATERAL (
                        SELECT count(*) FILTER (WHERE cantidad <> 0) AS renglones,
                               sum(cantidad) AS piezas,
                               count(*) FILTER (WHERE cantidad < 0) AS negativos
                          FROM existencias WHERE almacen_id = a.id
                  ) e ON true
                 WHERE a.activo
                 ORDER BY a.tipo, a.codigo
                """
            )
        )
    ).mappings().all()

    if not almacenes:
        return render(
            peticion,
            "inventario.html",
            {
                "almacenes": [],
                "elegido": None,
                "filas": [],
                "q": "",
                "filtro": filtro,
                "total_piezas": Decimal(0),
            },
            actor=actor,
            seccion="Inventario",
        )

    # El elegido, o la primera bodega: es donde está casi todo el inventario y la
    # pregunta más frecuente ("¿tengo para cargar mañana?") es sobre ella.
    elegido = None
    if almacen:
        try:
            buscado = uuid.UUID(almacen)
            elegido = next((a for a in almacenes if a["id"] == buscado), None)
        except ValueError:
            elegido = None
    if elegido is None:
        elegido = next((a for a in almacenes if a["tipo"] == "bodega"), almacenes[0])

    condiciones = ["e.almacen_id = :a"]
    parametros: dict[str, object] = {"a": elegido["id"]}

    if filtro == "con_existencia":
        condiciones.append("e.cantidad <> 0")
    elif filtro == "negativos":
        condiciones.append("e.cantidad < 0")

    busqueda = q.strip()
    if busqueda:
        condiciones.append("(p.nombre ILIKE :q OR p.sku ILIKE :q OR p.codigo_barras = :exacto)")
        parametros["q"] = f"%{busqueda}%"
        parametros["exacto"] = busqueda

    filas = (
        await sesion.execute(
            text(
                f"""
                SELECT p.id, p.sku, p.nombre, p.unidad_base, p.activo,
                       e.cantidad, e.actualizado_en,
                       pu.presentaciones,
                       -- La suma del libro mayor para este almacén y producto.
                       -- Si no cuadra con la caché, alguna transacción escribió
                       -- una y no la otra: es un bug, no un dato.
                       COALESCE(m.entradas, 0) - COALESCE(m.salidas, 0) AS segun_libro
                  FROM existencias e
                  JOIN productos p ON p.id = e.producto_id
                  LEFT JOIN LATERAL (
                        SELECT json_agg(json_build_object(
                                 'unidad', u.unidad_codigo, 'factor', u.factor::text)
                               ORDER BY u.factor DESC) AS presentaciones
                          FROM producto_unidades u
                         WHERE u.producto_id = p.id AND u.activo
                  ) pu ON true
                  LEFT JOIN LATERAL (
                        SELECT
                          sum(cantidad) FILTER (WHERE almacen_destino_id = e.almacen_id)
                            AS entradas,
                          sum(cantidad) FILTER (WHERE almacen_origen_id = e.almacen_id)
                            AS salidas
                          FROM movimientos_inventario
                         WHERE producto_id = e.producto_id
                           AND (almacen_origen_id = e.almacen_id
                                OR almacen_destino_id = e.almacen_id)
                  ) m ON true
                 WHERE {" AND ".join(condiciones)}
                 ORDER BY p.nombre
                 LIMIT 500
                """  # noqa: S608 — las condiciones son constantes del código
            ),
            parametros,
        )
    ).mappings().all()

    descuadres = [
        f for f in filas if Decimal(f["cantidad"]) != Decimal(f["segun_libro"])
    ]

    return render(
        peticion,
        "inventario.html",
        {
            "almacenes": almacenes,
            "elegido": elegido,
            "filas": filas,
            "descuadres": descuadres,
            "q": busqueda,
            "filtro": filtro,
            "total_piezas": sum((Decimal(f["cantidad"]) for f in filas), Decimal(0)),
        },
        actor=actor,
        seccion="Inventario",
    )


@router.get("/{almacen_id}/{producto_id}", response_class=HTMLResponse)
async def movimientos(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    almacen_id: uuid.UUID,
    producto_id: uuid.UUID,
) -> HTMLResponse:
    """El libro mayor de un producto en un almacén, con su saldo corriente.

    La pregunta que sigue a un número raro es siempre "¿cómo llegó ahí?". Sin esta
    pantalla la respuesta sale de psql, y entonces nadie la hace.

    El saldo corriente se calcula en orden cronológico: así se ve **en qué
    movimiento** el inventario se fue a negativo, que es distinto de saber que hoy
    está negativo.
    """
    actor.exigir("inventario.ver")

    cabecera = (
        await sesion.execute(
            text(
                "SELECT p.sku, p.nombre, p.unidad_base, a.nombre AS almacen, "
                "       a.codigo AS almacen_codigo, a.id AS almacen_id, "
                "       COALESCE(e.cantidad, 0) AS cantidad "
                "  FROM productos p "
                "  CROSS JOIN almacenes a "
                "  LEFT JOIN existencias e ON e.producto_id = p.id AND e.almacen_id = a.id "
                " WHERE p.id = :p AND a.id = :a"
            ),
            {"p": producto_id, "a": almacen_id},
        )
    ).mappings().first()
    if cabecera is None:
        return RedirectResponse("/panel/inventario", status_code=303)

    crudos = (
        await sesion.execute(
            text(
                """
                SELECT m.id, m.tipo, m.cantidad, m.lote, m.fecha_servidor,
                       m.documento_tipo, m.documento_id,
                       m.almacen_origen_id, m.almacen_destino_id,
                       o.codigo AS origen, d.codigo AS destino,
                       u.nombre AS quien
                  FROM movimientos_inventario m
                  LEFT JOIN almacenes o ON o.id = m.almacen_origen_id
                  LEFT JOIN almacenes d ON d.id = m.almacen_destino_id
                  LEFT JOIN usuarios u ON u.id = m.usuario_id
                 WHERE m.producto_id = :p
                   AND (m.almacen_origen_id = :a OR m.almacen_destino_id = :a)
                 ORDER BY m.fecha_servidor, m.id
                """
            ),
            {"p": producto_id, "a": almacen_id},
        )
    ).mappings().all()

    saldo = Decimal(0)
    renglones = []
    for m in crudos:
        entra = m["almacen_destino_id"] == almacen_id
        delta = Decimal(m["cantidad"]) if entra else -Decimal(m["cantidad"])
        saldo += delta
        renglones.append({**dict(m), "entra": entra, "delta": delta, "saldo": saldo})

    # Lo más reciente arriba para leer, pero el saldo se calculó en orden.
    renglones.reverse()

    return render(
        peticion,
        "inventario_movimientos.html",
        {
            "cabecera": cabecera,
            "renglones": renglones,
            "segun_libro": saldo,
            "cuadra": saldo == Decimal(cabecera["cantidad"]),
        },
        actor=actor,
        seccion="Inventario",
    )
