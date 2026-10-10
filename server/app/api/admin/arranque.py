"""¿Listo para operar?, y lo que queda pendiente hoy.

────────────────────────────────────────────────────────────────────────────
LAS DOS PREGUNTAS QUE LA OFICINA SE HACE, Y QUE EL PANEL NO CONTESTABA
────────────────────────────────────────────────────────────────────────────
El panel tiene veintitantas pantallas y cada una hace bien lo suyo. Lo que no
tenía era quien dijera **por dónde empezar**:

· **Antes de salir a la calle** —¿ya está todo?—. Un vendedor sin teléfono
  vinculado no sincroniza; una ruta sin titular manda sus clientes a nadie; un
  producto sin precio en la lista general no le aparece al vendedor. Cada hueco
  se descubre en la calle, con el cliente enfrente, y cuesta una llamada. Esta
  pantalla los revisa todos y dice dónde se arregla cada uno.
· **Cada mañana** —¿qué hay que hacer hoy?—. El tablero tenía las cifras; los
  pendientes estaban repartidos entre cinco pantallas. Ahora el tablero los
  lista arriba, en el orden del día, y solo los que tienen algo.

Los dos se leen con una consulta cada uno, sin tablas nuevas: todo lo que
revisan ya existía. Nada de esto escribe.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, dinero, permiso_de_la_pantalla, render
from app.api.admin.sesion_web import ActorWeb

router = APIRouter(prefix="/panel/arranque", tags=["panel"], include_in_schema=False)


@dataclass(frozen=True)
class Paso:
    titulo: str
    listo: bool
    # Bloqueante: sin esto la operación no arranca o se descuadra el primer día.
    # Recomendado: se puede operar, pero algo queda cojo (Efectividad, el cobro
    # de un faltante).
    bloqueante: bool
    detalle: str
    enlace: str
    accion: str


SQL_ARRANQUE = """
SELECT
  (SELECT count(*) FROM almacenes WHERE tipo = 'bodega' AND activo) AS bodegas,
  (SELECT count(*) FROM listas_precios WHERE es_default AND activo) AS listas_omision,
  (SELECT count(*) FROM productos WHERE activo) AS productos,
  (SELECT count(*) FROM productos p
    WHERE p.activo
      AND NOT EXISTS (
        SELECT 1 FROM precios pr
          JOIN listas_precios l ON l.id = pr.lista_id AND l.es_default
         WHERE pr.producto_id = p.id)) AS sin_precio,
  (SELECT COALESCE(sum(e.cantidad), 0) FROM existencias e
     JOIN almacenes a ON a.id = e.almacen_id AND a.tipo = 'bodega'
    WHERE e.cantidad > 0) AS existencia_bodega,
  (SELECT count(*) FROM productos p
    WHERE p.activo
      AND NOT EXISTS (SELECT 1 FROM producto_costos pc
                       WHERE pc.producto_id = p.id AND pc.costo_promedio > 0))
    AS sin_costo,
  (SELECT count(*) FROM usuarios WHERE rol_codigo = 'vendedor' AND activo) AS vendedores,
  (SELECT string_agg(u.codigo, ', ' ORDER BY u.codigo) FROM usuarios u
    WHERE u.rol_codigo = 'vendedor' AND u.activo AND u.almacen_id IS NULL) AS sin_camion,
  (SELECT string_agg(u.codigo, ', ' ORDER BY u.codigo) FROM usuarios u
    WHERE u.rol_codigo = 'vendedor' AND u.activo
      AND NOT EXISTS (SELECT 1 FROM usuarios_rutas ur WHERE ur.usuario_id = u.id))
    AS sin_ruta,
  (SELECT string_agg(u.codigo, ', ' ORDER BY u.codigo) FROM usuarios u
    WHERE u.rol_codigo = 'vendedor' AND u.activo
      AND NOT EXISTS (SELECT 1 FROM dispositivos d
                       WHERE d.usuario_id = u.id AND d.estado = 'activo'))
    AS sin_telefono,
  (SELECT string_agg(r.codigo, ', ' ORDER BY r.codigo) FROM rutas r
    WHERE r.activo AND r.vendedor_id IS NULL
      AND EXISTS (SELECT 1 FROM clientes c
                   WHERE c.ruta_id = r.id AND c.estatus NOT IN ('inactivo', 'baja')))
    AS rutas_sin_titular,
  (SELECT count(*) FROM clientes WHERE estatus NOT IN ('inactivo', 'baja')) AS clientes,
  (SELECT count(*) FROM clientes
    WHERE estatus NOT IN ('inactivo', 'baja') AND ruta_id IS NULL) AS clientes_sin_ruta,
  (SELECT count(*) FROM clientes
    WHERE estatus NOT IN ('inactivo', 'baja') AND ruta_id IS NOT NULL
      AND plan_visita = '[]'::jsonb) AS clientes_sin_plan,
  (SELECT count(*) FROM motivos_merma WHERE activo) AS motivos_merma,
  (SELECT count(*) FROM motivos_no_drop WHERE activo) AS motivos_no_drop
"""


def _plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


async def revisar_arranque(sesion) -> list[Paso]:
    """Los pasos en el orden en que se hacen: sin bodega no hay entrada, sin
    entrada no hay carga, y sin vendedor completo la carga no sale."""
    f = (await sesion.execute(text(SQL_ARRANQUE))).mappings().one()

    vendedores_completos = f["vendedores"] > 0 and not (f["sin_camion"] or f["sin_ruta"])
    huecos_vendedor = []
    if f["sin_camion"]:
        huecos_vendedor.append(f"sin camión: {f['sin_camion']}")
    if f["sin_ruta"]:
        huecos_vendedor.append(f"sin ruta: {f['sin_ruta']}")

    return [
        Paso(
            titulo="Una bodega activa",
            listo=f["bodegas"] > 0,
            bloqueante=True,
            detalle=(
                "De la bodega salen las cargas y a ella regresa lo que baja del camión."
                if f["bodegas"] == 0
                else _plural(f["bodegas"], "bodega activa.", "bodegas activas.")
            ),
            enlace="/panel/equipo",
            accion="Dar de alta la bodega",
        ),
        Paso(
            titulo="Una lista de precios por omisión",
            listo=f["listas_omision"] > 0,
            bloqueante=True,
            detalle=(
                "Con ella se le cotiza al cliente que no tiene lista, como el que el "
                "vendedor da de alta en la calle."
                if f["listas_omision"] == 0
                else "Lista. El cliente sin lista propia se cotiza con ella."
            ),
            enlace="/panel/equipo",
            accion="Marcar una lista como la de por omisión",
        ),
        Paso(
            titulo="Los productos, con precio",
            listo=f["productos"] > 0 and f["sin_precio"] == 0,
            bloqueante=True,
            detalle=(
                "Todavía no hay productos."
                if f["productos"] == 0
                else (
                    f"{_plural(f['sin_precio'], 'producto', 'productos')} sin precio en "
                    "la lista general: al vendedor no le aparecen."
                    if f["sin_precio"]
                    else _plural(f["productos"], "producto activo", "productos activos")
                    + ", todos con precio."
                )
            ),
            enlace=(
                "/panel/productos?filtro=sin_precio" if f["sin_precio"] else "/panel/productos"
            ),
            accion="Ponerles precio" if f["sin_precio"] else "Dar de alta productos",
        ),
        Paso(
            titulo="La existencia inicial en bodega",
            listo=f["existencia_bodega"] > 0,
            bloqueante=True,
            detalle=(
                "La bodega está en ceros: sin una entrada, no hay nada que cargar."
                if f["existencia_bodega"] <= 0
                else "Hay mercancía en bodega."
            ),
            enlace="/panel/entradas",
            accion="Capturar la entrada inicial",
        ),
        Paso(
            titulo="El costo de los productos",
            listo=f["productos"] > 0 and f["sin_costo"] == 0,
            bloqueante=False,
            detalle=(
                f"{_plural(f['sin_costo'], 'producto', 'productos')} sin costo. El costo "
                "sale de las entradas con factura; sin él, un faltante en el corte se "
                "le cargaría al vendedor en $0."
                if f["sin_costo"]
                else "Todos tienen costo."
            ),
            enlace="/panel/entradas",
            accion="Capturar entradas con su costo",
        ),
        Paso(
            titulo="Cada vendedor con su camión y su ruta",
            listo=vendedores_completos,
            bloqueante=True,
            detalle=(
                "Todavía no hay vendedores."
                if f["vendedores"] == 0
                else (
                    "Les falta " + "; ".join(huecos_vendedor) + "."
                    if huecos_vendedor
                    else _plural(f["vendedores"], "vendedor completo.", "vendedores completos.")
                )
            ),
            enlace="/panel/equipo",
            accion="Completar en Usuarios y rutas",
        ),
        Paso(
            titulo="Cada vendedor con su teléfono vinculado",
            listo=f["vendedores"] > 0 and not f["sin_telefono"],
            bloqueante=True,
            detalle=(
                f"Sin teléfono: {f['sin_telefono']}. Sin él no sincroniza: ni recibe la "
                "carga ni sube lo que vende."
                if f["sin_telefono"]
                else "Todos tienen un teléfono activo."
            ),
            enlace="/panel/equipos",
            accion="Vincular los teléfonos",
        ),
        Paso(
            titulo="Cada ruta con clientes tiene titular",
            listo=not f["rutas_sin_titular"],
            bloqueante=True,
            detalle=(
                f"Sin titular: {f['rutas_sin_titular']}. Sus clientes no le llegan a "
                "ningún teléfono."
                if f["rutas_sin_titular"]
                else "Todas tienen quién las trabaje."
            ),
            enlace="/panel/equipo",
            accion="Asignar titulares",
        ),
        Paso(
            titulo="Los clientes, cada uno en su ruta",
            listo=f["clientes"] > 0 and f["clientes_sin_ruta"] == 0,
            bloqueante=True,
            detalle=(
                "Todavía no hay clientes."
                if f["clientes"] == 0
                else (
                    f"{_plural(f['clientes_sin_ruta'], 'cliente', 'clientes')} sin ruta: "
                    "no le llegan a ningún vendedor."
                    if f["clientes_sin_ruta"]
                    else _plural(f["clientes"], "cliente", "clientes") + ", todos con ruta."
                )
            ),
            enlace="/panel/clientes?filtro=todos",
            accion="Asignarles ruta",
        ),
        Paso(
            titulo="El plan de visita",
            listo=f["clientes"] > 0 and f["clientes_sin_plan"] == 0,
            bloqueante=False,
            detalle=(
                f"{_plural(f['clientes_sin_plan'], 'cliente', 'clientes')} sin día de "
                "visita. El vendedor puede trabajar sin plan, pero el teléfono no le "
                "dice «hoy te tocan estos» y Efectividad no puede contar a quién no se "
                "visitó."
                if f["clientes_sin_plan"]
                else "Todos los clientes tienen día de visita."
            ),
            enlace="/panel/plan-visita",
            accion="Planear las rutas",
        ),
        Paso(
            titulo="Los motivos de merma y de no-venta",
            listo=f["motivos_merma"] > 0 and f["motivos_no_drop"] > 0,
            bloqueante=True,
            detalle=(
                "Sin ellos el vendedor no puede registrar una merma ni una visita sin "
                "venta."
                if not (f["motivos_merma"] and f["motivos_no_drop"])
                else f"{f['motivos_merma']} de merma y {f['motivos_no_drop']} de no-venta."
            ),
            enlace="/panel/motivos",
            accion="Revisar los motivos",
        ),
    ]


def faltan_para_operar(pasos: list[Paso]) -> int:
    return sum(1 for p in pasos if p.bloqueante and not p.listo)


@router.get("", response_class=HTMLResponse)
async def arranque(peticion: Request, actor: ActorWeb, sesion: SesionDep) -> HTMLResponse:
    pasos = await revisar_arranque(sesion)
    return render(
        peticion,
        "arranque.html",
        {
            "pasos": pasos,
            "faltan": faltan_para_operar(pasos),
            "listos": sum(1 for p in pasos if p.listo),
        },
        actor=actor,
        seccion="Tablero",
    )


# ---------------------------------------------------------------------------
# Los pendientes de hoy
# ---------------------------------------------------------------------------

SQL_PENDIENTES = """
SELECT
  -- Lo que mandan los vendedores al terminar el día (§82): su corte y la
  -- carga que piden para mañana.
  (SELECT count(*) FROM cortes_vendedor WHERE estado = 'pendiente') AS cortes_por_cerrar,
  (SELECT count(*) FROM solicitudes_carga WHERE estado = 'pendiente') AS cargas_pedidas,
  (SELECT count(*) FROM cargas
    WHERE estado = 'borrador' AND fecha_operativa <= CURRENT_DATE) AS cargas_borrador,
  (SELECT count(*) FROM cargas
    WHERE estado IN ('confirmada', 'en_ruta')
      AND fecha_operativa < CURRENT_DATE) AS cortes_atrasados,
  (SELECT count(*) FROM sync_cuarentena WHERE estado = 'pendiente') AS cuarentena,
  (SELECT count(*) FROM dispositivos
    WHERE estado = 'activo'
      AND (ultima_sync_push_en IS NULL
           OR ultima_sync_push_en < now() - interval '1 day')) AS telefonos_callados,
  (SELECT count(*) FROM clientes WHERE estatus = 'prospecto') AS prospectos,
  (SELECT count(*) FROM traspasos t
     JOIN almacenes o ON o.id = t.almacen_origen_id AND o.tipo = 'camion'
    WHERE t.estado = 'propuesto') AS devoluciones,
  (SELECT count(*) FROM ventas
    WHERE pago_estado = 'por_confirmar' AND estado = 'confirmada') AS por_confirmar,
  (SELECT COALESCE(sum(total), 0) FROM ventas
    WHERE pago_estado = 'por_confirmar' AND estado = 'confirmada') AS importe_por_confirmar,
  (SELECT count(*) FROM ventas
    WHERE requiere_revision AND estado = 'confirmada') AS ventas_revision
"""


@dataclass(frozen=True)
class Pendiente:
    momento: str
    cuantos: int
    texto: str
    enlace: str
    urgente: bool = False


async def pendientes_de_hoy(sesion, actor) -> list[Pendiente]:
    """Lo que hay que hacer hoy, en el orden del día, y SOLO lo que tiene algo.

    Una lista de pendientes que siempre muestra diez renglones en cero se deja de
    leer el día tres. Esta muestra únicamente lo que espera a alguien, y cada
    renglón lleva a la pantalla donde se resuelve. Lo que la persona no puede
    abrir no se le lista: sería un pendiente que no puede atender.
    """
    f = (await sesion.execute(text(SQL_PENDIENTES))).mappings().one()
    todos = [
        Pendiente(
            "Antes de que salgan los camiones",
            f["cortes_por_cerrar"],
            "corte(s) de vendedor por cerrar: cuenta el efectivo que entregó.",
            "/panel/cierres",
            urgente=True,
        ),
        Pendiente(
            "Antes de que salgan los camiones",
            f["cargas_pedidas"],
            "carga(s) pedida(s) por los vendedores, esperando que la aceptes.",
            "/panel/cierres",
            urgente=True,
        ),
        Pendiente(
            "Antes de que salgan los camiones",
            f["cargas_borrador"],
            "carga(s) en borrador: el teléfono no las recibe hasta que se confirman.",
            "/panel/cargas",
            urgente=True,
        ),
        Pendiente(
            "Antes de que salgan los camiones",
            f["cortes_atrasados"],
            "corte(s) de días anteriores sin cerrar: el dinero y la mercancía de esa "
            "salida siguen sin cuadrar.",
            "/panel/liquidaciones",
            urgente=True,
        ),
        Pendiente(
            "Durante el día",
            f["cuarentena"],
            "operación(es) en cuarentena: el vendedor ya entregó y la oficina no la "
            "tiene registrada.",
            "/panel/cuarentena",
            urgente=True,
        ),
        Pendiente(
            "Durante el día",
            f["telefonos_callados"],
            "teléfono(s) con más de un día sin sincronizar: lo que vendieron no se ve.",
            "/panel/equipos",
        ),
        Pendiente(
            "Durante el día",
            f["prospectos"],
            "cliente(s) dado(s) de alta en la calle, esperando código y lista de precios.",
            "/panel/clientes",
        ),
        Pendiente(
            "Al cierre",
            f["devoluciones"],
            "devolución(es) de camión por recibir en bodega: la mercancía está en "
            "tránsito y no cuenta en ningún lado.",
            "/panel/entradas",
        ),
        Pendiente(
            "Al cierre",
            f["por_confirmar"],
            f"transferencia(s) por confirmar ({dinero(f['importe_por_confirmar'])}) contra "
            "el banco: vienen de una app anterior, y hasta confirmarlas la caja no cuadra.",
            "/panel/transferencias",
        ),
        Pendiente(
            "Al cierre",
            f["ventas_revision"],
            "venta(s) marcada(s) para revisión: precio viejo, fuera de geocerca, "
            "sin ubicación.",
            "/panel/ventas",
        ),
    ]

    def puede_abrir(enlace: str) -> bool:
        permiso = permiso_de_la_pantalla(enlace)
        return permiso is None or actor.puede(permiso)

    return [p for p in todos if p.cuantos and puede_abrir(p.enlace)]
