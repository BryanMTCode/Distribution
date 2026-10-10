"""La empresa en una pantalla: cuántos clientes, vendedores, artículos, etc.

Pedido en operación (octubre 2026): «tanto en el dashboard como en la app de
gerente quiero un resumen de la empresa: cuántos clientes hay, cuántos
vendedores, artículos…». No es el día —eso es el Tablero— sino el tamaño del
negocio: lo que se tiene, no lo que pasó hoy.

La consulta vive aquí, una vez, y la usan el panel (`/panel/empresa`) y la app
(`/v1/tablero/empresa`). Cada cifra del panel enlaza a la pantalla donde se ve
el detalle.

No incluye el VALOR del inventario a propósito: el costo es dato reservado de
compras (migración 0027), y esta pantalla la ve también el supervisor.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.sesion_web import ActorWeb

router = APIRouter(prefix="/panel/empresa", tags=["panel"], include_in_schema=False)

PERMISO = "tablero.ver"

SQL_RESUMEN = """
SELECT
  -- Clientes
  (SELECT count(*) FROM clientes WHERE estatus NOT IN ('inactivo', 'prospecto'))
    AS clientes_activos,
  (SELECT count(*) FROM clientes WHERE estatus = 'prospecto') AS prospectos,
  (SELECT count(*) FROM clientes WHERE estatus = 'inactivo') AS clientes_inactivos,
  (SELECT count(*) FROM clientes
    WHERE creado_en >= date_trunc('month', CURRENT_DATE)) AS clientes_nuevos_mes,
  -- Gente y rutas
  (SELECT count(*) FROM usuarios WHERE activo AND rol_codigo = 'vendedor') AS vendedores,
  (SELECT count(*) FROM usuarios u JOIN almacenes a ON a.id = u.almacen_id
    WHERE u.activo AND u.rol_codigo = 'vendedor' AND a.tipo = 'camion')
    AS vendedores_con_camion,
  (SELECT count(*) FROM usuarios
    WHERE activo AND rol_codigo IN ('admin', 'gerente', 'supervisor')) AS usuarios_oficina,
  (SELECT count(*) FROM rutas WHERE activo) AS rutas,
  (SELECT count(*) FROM dispositivos WHERE estado = 'activo') AS telefonos,
  -- Artículos e inventario
  (SELECT count(*) FROM productos WHERE activo) AS productos,
  (SELECT count(*) FROM productos p
    WHERE p.activo
      AND NOT EXISTS (
        SELECT 1 FROM precios pr
          JOIN listas_precios l ON l.id = pr.lista_id AND l.es_default
         WHERE pr.producto_id = p.id)) AS productos_sin_precio,
  (SELECT count(*) FROM almacenes WHERE activo AND tipo = 'bodega') AS bodegas,
  (SELECT count(*) FROM almacenes WHERE activo AND tipo = 'camion') AS camiones,
  (SELECT COALESCE(sum(e.cantidad), 0) FROM existencias e
     JOIN almacenes a ON a.id = e.almacen_id
    WHERE a.tipo = 'bodega' AND e.cantidad > 0) AS piezas_en_bodegas,
  (SELECT COALESCE(sum(e.cantidad), 0) FROM existencias e
     JOIN almacenes a ON a.id = e.almacen_id
    WHERE a.tipo = 'camion' AND e.cantidad > 0) AS piezas_en_camiones,
  (SELECT count(*) FROM existencias e
     JOIN almacenes a ON a.id = e.almacen_id
    WHERE a.tipo IN ('bodega', 'camion') AND e.cantidad < 0) AS existencias_negativas,
  -- Dinero. Todo es de contado (ADR 0002 §81): no hay cartera que contar.
  (SELECT COALESCE(sum(total), 0) FROM ventas
    WHERE estado = 'confirmada'
      AND fecha_operativa >= date_trunc('month', CURRENT_DATE)) AS vendido_mes,
  (SELECT COALESCE(sum(total), 0) FROM ventas
    WHERE estado = 'confirmada'
      AND fecha_operativa >= date_trunc('year', CURRENT_DATE)) AS vendido_anio
"""


async def resumen_de_la_empresa(sesion) -> dict:
    return dict((await sesion.execute(text(SQL_RESUMEN))).mappings().one())


@router.get("", response_class=HTMLResponse)
async def ver(peticion: Request, actor: ActorWeb, sesion: SesionDep) -> HTMLResponse:
    actor.exigir(PERMISO)
    return render(
        peticion,
        "empresa.html",
        {"r": await resumen_de_la_empresa(sesion)},
        actor=actor,
        seccion="Tablero",
    )
