"""Productos → Costos: el costo de todos los artículos, de un jalón (ADR 0002 §180).

El costo de un artículo es el promedio ponderado de lo que se compró
(`producto_costos`, migración 0027), y se mueve solo con cada entrada que trae
costo. Pero lo que entró sin costo —el conteo de arranque, un ajuste— dejó
artículos sin él, y sin costo no hay ganancia que decir. Aquí se escribe: por
pieza, o por caja y el sistema lo divide.

Solo quien tiene `costos.ver`, que es el administrador. Cada cambio queda en la
auditoría con el costo de antes y el de después.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.api.admin.comun import (
    DIEZMILESIMA,
    MAXIMO_CAMPOS_DE_RENGLONES,
    CapturaInvalida,
    SesionDep,
    agrupar_por_familia,
    auditar,
    leer_precio,
    render,
)
from app.api.admin.sesion_web import ActorWeb, exigir_csrf
from app.infra.valor_de_inventario import precio_de_venta

router = APIRouter(prefix="/panel/costos", tags=["panel"], include_in_schema=False)

PERMISO = "costos.ver"

SQL_CATALOGO = f"""
SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_base,
       cat.id AS familia_id, cat.nombre AS familia,
       pc.costo_promedio AS costo, pc.ultima_compra_en,
       pv.precio,
       caja.unidad_codigo AS caja, caja.factor AS factor_caja
  FROM productos p
  LEFT JOIN categorias cat ON cat.id = p.categoria_id
  LEFT JOIN producto_costos pc ON pc.producto_id = p.id
  LEFT JOIN LATERAL (
        SELECT u.unidad_codigo, u.factor FROM producto_unidades u
         WHERE u.producto_id = p.id AND u.activo AND u.factor > 1
         ORDER BY u.factor DESC LIMIT 1
  ) caja ON true
  {precio_de_venta("p.id")}
 WHERE p.activo
   AND (NOT :faltan OR pc.producto_id IS NULL)
   AND (CAST(:q AS text) IS NULL OR p.nombre ILIKE :q OR p.sku ILIKE :q
        OR p.codigo_barras = :exacto)
 ORDER BY (cat.id IS NULL), cat.orden, cat.nombre, p.nombre
"""


@router.get("", response_class=HTMLResponse)
async def pantalla(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    buscar: str = "",
    faltan: str = "",
    guardado: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    return await _pintar(peticion, actor, sesion, buscar=buscar, faltan=bool(faltan),
                         guardado=guardado)


@router.post("")
async def guardar(peticion: Request, actor: ActorWeb, sesion: SesionDep):
    """Guarda lo que se escribió. Todo o nada: si un renglón está mal, no se
    guarda ninguno y la pantalla regresa con lo escrito."""
    actor.exigir(PERMISO)
    formulario = await peticion.form(max_fields=MAXIMO_CAMPOS_DE_RENGLONES)
    exigir_csrf(peticion, str(formulario.get("csrf", "")))
    buscar = str(formulario.get("buscar", ""))
    faltan = bool(formulario.get("faltan"))
    capturado = {
        clave: str(valor)
        for clave, valor in formulario.multi_items()
        if clave.startswith(("pieza_", "caja_"))
    }
    try:
        costos = await _leer_costos(sesion, capturado)
    except CapturaInvalida as e:
        return await _pintar(peticion, actor, sesion, buscar=buscar, faltan=faltan,
                             error=str(e), capturado=capturado)

    antes = {
        f["producto_id"]: f["costo_promedio"]
        for f in (
            await sesion.execute(
                text("SELECT producto_id, costo_promedio FROM producto_costos "
                     " WHERE producto_id = ANY(:ids)"),
                {"ids": list(costos)},
            )
        ).mappings()
    }
    cambiados = 0
    for producto_id, costo in costos.items():
        if antes.get(producto_id) == costo:
            continue
        await sesion.execute(
            text(
                """
                INSERT INTO producto_costos (producto_id, costo_promedio, actualizado_en)
                VALUES (:p, :c, now())
                ON CONFLICT (producto_id) DO UPDATE
                   SET costo_promedio = EXCLUDED.costo_promedio, actualizado_en = now()
                """
            ),
            {"p": producto_id, "c": costo},
        )
        await auditar(
            sesion, entidad="producto", entidad_id=producto_id, accion="capturar_costo",
            quien=actor.usuario_id,
            antes={"costo": antes.get(producto_id)}, despues={"costo": costo},
        )
        cambiados += 1
    await sesion.commit()

    consulta = {k: v for k, v in (("buscar", buscar.strip()), ("faltan", "1" if faltan else ""))
                if v}
    mensaje = (
        f"Se guardó el costo de {cambiados} artículo(s)." if cambiados
        else "No cambió ningún costo."
    )
    return RedirectResponse(
        f"/panel/costos?{urlencode(consulta)}&guardado={quote(mensaje)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _leer_costos(sesion, capturado: dict[str, str]) -> dict[uuid.UUID, Decimal]:
    """El costo por pieza de cada renglón con algo escrito: el de la pieza, o el
    de la caja entre lo que trae. El factor sale del catálogo, no del formulario.
    Si un renglón está mal, se dicen todos los que lo están."""
    ids: set[uuid.UUID] = set()
    for clave, valor in capturado.items():
        if not valor.strip():
            continue
        try:
            ids.add(uuid.UUID(clave.split("_", 1)[1]))
        except ValueError:
            continue
    if not ids:
        raise CapturaInvalida("No escribiste ningún costo.")
    filas = {
        f["id"]: f
        for f in (
            await sesion.execute(
                text(
                    "SELECT p.id, p.nombre, "
                    "       (SELECT u.factor FROM producto_unidades u "
                    "         WHERE u.producto_id = p.id AND u.activo AND u.factor > 1 "
                    "         ORDER BY u.factor DESC LIMIT 1) AS factor_caja "
                    "  FROM productos p WHERE p.id = ANY(:ids)"
                ),
                {"ids": list(ids)},
            )
        ).mappings()
    }
    costos: dict[uuid.UUID, Decimal] = {}
    malos: list[str] = []
    for producto_id in ids:
        fila = filas.get(producto_id)
        if fila is None:
            malos.append("un artículo que ya no existe")
            continue
        pieza = capturado.get(f"pieza_{producto_id}", "").strip()
        caja = capturado.get(f"caja_{producto_id}", "").strip()
        try:
            if pieza and caja:
                raise CapturaInvalida(
                    f"{fila['nombre']}: escribe el costo de la pieza o el de la caja, no los dos"
                )
            if pieza:
                costos[producto_id] = leer_precio(pieza, campo=f"El costo de {fila['nombre']}")
            elif fila["factor_caja"] is None:
                raise CapturaInvalida(f"{fila['nombre']} no tiene caja: escríbelo por pieza")
            else:
                de_la_caja = leer_precio(caja, campo=f"El costo de la caja de {fila['nombre']}")
                costos[producto_id] = (de_la_caja / Decimal(fila["factor_caja"])).quantize(
                    DIEZMILESIMA, rounding=ROUND_HALF_UP
                )
        except CapturaInvalida as e:
            malos.append(str(e).rstrip("."))
    if malos:
        raise CapturaInvalida(f"Revisa: {'; '.join(malos)}. No se guardó nada.")
    return costos


async def _pintar(
    peticion: Request,
    actor,
    sesion,
    *,
    buscar: str = "",
    faltan: bool = False,
    guardado: str = "",
    error: str = "",
    capturado: dict[str, str] | None = None,
) -> HTMLResponse:
    busqueda = buscar.strip()
    filas = [
        dict(f)
        for f in (
            await sesion.execute(
                text(SQL_CATALOGO),
                {"faltan": faltan, "q": f"%{busqueda}%" if busqueda else None,
                 "exacto": busqueda},
            )
        ).mappings()
    ]
    for f in filas:
        if f["costo"] is not None and f["precio"] is not None:
            f["ganancia"] = Decimal(f["precio"]) - Decimal(f["costo"])
            f["margen"] = (f["ganancia"] * 100 / Decimal(f["precio"])
                           if Decimal(f["precio"]) > 0 else None)
        else:
            f["ganancia"] = f["margen"] = None
    sin_costo = (
        await sesion.execute(
            text("SELECT count(*) FROM productos p WHERE p.activo AND NOT EXISTS "
                 "(SELECT 1 FROM producto_costos c WHERE c.producto_id = p.id)")
        )
    ).scalar_one()
    return render(
        peticion,
        "costos.html",
        {
            "grupos": agrupar_por_familia(filas),
            "buscar": busqueda,
            "faltan": faltan,
            "sin_costo": sin_costo,
            "capturado": capturado or {},
            "guardado": guardado,
            "error": error,
        },
        actor=actor,
        seccion="Productos",
    )
