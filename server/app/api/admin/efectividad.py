"""Efectividad de visita: lo que la Fase 6 empezó a capturar, leído.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA PANTALLA ES LA OTRA MITAD DEL NO-DROP
────────────────────────────────────────────────────────────────────────────
El no-drop existe porque sin él un día de 20 visitas con 12 ventas se ve igual que
uno de 12 visitas con 12 ventas. Pero capturar el dato y no leerlo deja el problema
intacto: el vendedor dedica tiempo a registrar visitas perdidas y nadie las mira,
y en cuanto eso se nota, deja de registrarlas. Es el mismo problema de marcar una
venta para revisión sin que nadie revise.

────────────────────────────────────────────────────────────────────────────
LA PREGUNTA QUE CONTESTA, Y QUE NINGÚN OTRO REPORTE PUEDE
────────────────────────────────────────────────────────────────────────────
No es "cuánto vendimos" —eso lo dice el reporte de ventas— sino **cuántas visitas
perdidas podemos arreglar nosotros**:

    cliente    cerrado, no estaba quien decide, no tiene dinero hoy
    operación  se le acabó el crédito
    producto   no traigo lo que pidió, le pareció caro
    vendedor   no alcancé a visitarlo

Las tres últimas son nuestras. Un día con 8 no-drops por "no traigo lo que pidió"
no es un problema de ventas: es un problema de carga, y se arregla en la bodega a
la mañana siguiente. Sin la categoría, las ocho se verían como "no compró".

────────────────────────────────────────────────────────────────────────────
UNA VISITA ES UNA VENTA O UN NO-DROP
────────────────────────────────────────────────────────────────────────────
No hay tabla `visitas`, y no hace falta: los dos documentos cubren los dos
desenlaces posibles de pararse frente a una tienda. La efectividad es

    ventas / (ventas + no-drops)

y se cuenta por **cliente visitado**, no por documento: dos ventas al mismo cliente
el mismo día son una visita, y contarlas como dos inflaría la efectividad del
vendedor que parte un pedido en dos remisiones.

────────────────────────────────────────────────────────────────────────────
EL RANGO POR OMISIÓN SON SIETE DÍAS, NO HOY
────────────────────────────────────────────────────────────────────────────
Una efectividad del 60% sobre 20 visitas y una del 60% sobre 140 son dos cosas
distintas, y la primera es ruido: con 20 visitas, dos clientes cerrados mueven el
número diez puntos. Abrir en "hoy" invitaría a tomar decisiones sobre esa clase de
número justo cuando el día todavía no termina de sincronizar (§0.3).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.api.admin.comun import SesionDep, render
from app.api.admin.sesion_web import ActorWeb

router = APIRouter(prefix="/panel/efectividad", tags=["panel"], include_in_schema=False)

PERMISO = "ventas.ver_todas"

# Siete días: con menos volumen el porcentaje es ruido. Ver el encabezado.
DIAS_POR_OMISION = 7

# Qué categorías de no-drop puede arreglar la empresa.
#
# Es la línea que convierte el reporte en algo accionable: 'cliente' es el mundo, y
# las otras tres son decisiones nuestras —qué se cargó, a quién se le dio crédito,
# cómo se planeó la ruta—.
NUESTRA_CULPA = ("operacion", "producto", "vendedor")

ETIQUETA_CATEGORIA = {
    "cliente": "Del cliente",
    "operacion": "De la operación",
    "producto": "Del producto o la carga",
    "vendedor": "Del vendedor",
}


def _rango(desde: str, hasta: str) -> tuple[date, date]:
    """Lee el rango del formulario, con omisión de siete días.

    Un rango invertido se endereza en vez de devolver una pantalla vacía: quien
    teclea las fechas al revés quiere ver ese periodo, no un error.
    """
    hoy = date.today()
    try:
        fin = date.fromisoformat(hasta) if hasta else hoy
    except ValueError:
        fin = hoy
    try:
        inicio = (
            date.fromisoformat(desde)
            if desde
            else fin - timedelta(days=DIAS_POR_OMISION - 1)
        )
    except ValueError:
        inicio = fin - timedelta(days=DIAS_POR_OMISION - 1)
    if inicio > fin:
        inicio, fin = fin, inicio
    return inicio, fin


def _porcentaje(parte: int, total: int) -> Decimal:
    """Un porcentaje con un decimal. Cero visitas da cero, no una división por cero."""
    if not total:
        return Decimal("0.0")
    return (Decimal(parte) * 100 / Decimal(total)).quantize(Decimal("0.1"))


@router.get("", response_class=HTMLResponse)
async def listar(
    peticion: Request,
    actor: ActorWeb,
    sesion: SesionDep,
    desde: str = "",
    hasta: str = "",
    ruta: str = "",
) -> HTMLResponse:
    actor.exigir(PERMISO)
    inicio, fin = _rango(desde, hasta)
    parametros = {"desde": inicio, "hasta": fin, "ruta": ruta}

    # ------------------------------------------------------------------
    # Por vendedor: visitas, ventas, efectividad.
    # ------------------------------------------------------------------
    # Se cuentan CLIENTES DISTINTOS, no documentos: dos remisiones al mismo cliente
    # el mismo día son una visita, y contarlas por separado premiaría al vendedor
    # que parte un pedido en dos.
    #
    # El `FULL OUTER JOIN` existe porque los dos lados pueden faltar: un vendedor
    # que vendió a todos no tiene no-drops, y uno que no vendió nada no tiene
    # ventas. Con un JOIN normal, el primero desaparecería del reporte justo por
    # haber tenido un día perfecto.
    por_vendedor = (
        await sesion.execute(
            text(
                """
                WITH visitas AS (
                    SELECT v.vendedor_id, v.cliente_id, v.fecha_operativa,
                           true AS vendio
                      FROM ventas v
                      LEFT JOIN clientes c ON c.id = v.cliente_id
                      LEFT JOIN rutas r ON r.id = c.ruta_id
                     WHERE v.estado = 'confirmada'
                       AND v.fecha_operativa BETWEEN :desde AND :hasta
                       AND (:ruta = '' OR r.codigo = :ruta)
                    UNION ALL
                    SELECT n.vendedor_id, n.cliente_id, n.fecha_operativa,
                           false AS vendio
                      FROM no_drops n
                      LEFT JOIN clientes c ON c.id = n.cliente_id
                      LEFT JOIN rutas r ON r.id = c.ruta_id
                     WHERE n.fecha_operativa BETWEEN :desde AND :hasta
                       AND (:ruta = '' OR r.codigo = :ruta)
                ),
                -- Una visita por (vendedor, cliente, día). Si ese día hubo venta
                -- y también no-drop del mismo cliente —pasó a la segunda vuelta—
                -- cuenta como vendida: el desenlace fue la venta.
                unicas AS (
                    SELECT vendedor_id, cliente_id, fecha_operativa,
                           bool_or(vendio) AS vendio
                      FROM visitas
                     GROUP BY vendedor_id, cliente_id, fecha_operativa
                )
                SELECT u.nombre AS vendedor, u.codigo AS vendedor_codigo,
                       count(*) AS visitas,
                       count(*) FILTER (WHERE x.vendio) AS ventas,
                       count(*) FILTER (WHERE NOT x.vendio) AS perdidas
                  FROM unicas x
                  JOIN usuarios u ON u.id = x.vendedor_id
                 GROUP BY u.id, u.nombre, u.codigo
                 ORDER BY visitas DESC
                """
            ),
            parametros,
        )
    ).mappings().all()

    # ------------------------------------------------------------------
    # Los motivos, con su categoría. Es el corazón del reporte.
    # ------------------------------------------------------------------
    motivos = (
        await sesion.execute(
            text(
                """
                SELECT m.codigo, m.nombre, m.categoria, count(*) AS cuantas,
                       count(DISTINCT n.cliente_id) AS clientes
                  FROM no_drops n
                  JOIN motivos_no_drop m ON m.codigo = n.motivo_codigo
                  LEFT JOIN clientes c ON c.id = n.cliente_id
                  LEFT JOIN rutas r ON r.id = c.ruta_id
                 WHERE n.fecha_operativa BETWEEN :desde AND :hasta
                   AND (:ruta = '' OR r.codigo = :ruta)
                 GROUP BY m.codigo, m.nombre, m.categoria
                 ORDER BY cuantas DESC, m.nombre
                """
            ),
            parametros,
        )
    ).mappings().all()

    # ------------------------------------------------------------------
    # Las pérdidas por motivo de merma.
    # ------------------------------------------------------------------
    # Vive en esta pantalla porque tiene el mismo problema que el no-drop: el
    # vendedor captura el motivo y hasta hoy nadie lo sumaba. `afecta_vendedor` es
    # lo que decide si la pérdida se le descuenta, así que la columna que importa no
    # es cuánto se perdió sino cuánto se le está cobrando a alguien.
    mermas = (
        await sesion.execute(
            text(
                """
                SELECT mm.codigo, mm.nombre, mm.afecta_vendedor,
                       count(DISTINCT m.id) AS documentos,
                       COALESCE(sum(d.cantidad_base), 0) AS unidades
                  FROM mermas m
                  JOIN motivos_merma mm ON mm.codigo = m.motivo_codigo
                  LEFT JOIN merma_detalle d ON d.merma_id = m.id
                 WHERE m.estado = 'confirmada'
                   AND m.tipo = 'merma'
                   AND m.fecha_operativa BETWEEN :desde AND :hasta
                 GROUP BY mm.codigo, mm.nombre, mm.afecta_vendedor
                 ORDER BY unidades DESC, mm.nombre
                """
            ),
            # Sin el filtro de ruta, y a propósito: una merma pertenece a un camión,
            # no a una ruta. Filtrarla por la ruta del cliente sería inventar una
            # relación que no existe, y además dejaría fuera justo las que no tienen
            # cliente — que son casi todas, porque la caja se revienta entre tiendas.
            {"desde": inicio, "hasta": fin},
        )
    ).mappings().all()

    # ------------------------------------------------------------------
    # Los cambios físicos (migración 0040): fresco por caducado o dañado.
    # ------------------------------------------------------------------
    # Aparte de las mermas porque NUNCA se le cobran al vendedor —el producto se
    # echó a perder en la tienda, no en su camión—, y mezclarlos haría que la
    # columna «¿se le descuenta?» mintiera. Por cliente y producto, que es la
    # pregunta que hace la oficina: ¿a quién le estamos cambiando, y qué?
    cambios = (
        await sesion.execute(
            text(
                """
                SELECT c.nombre_comercial AS cliente, p.nombre AS producto,
                       mm.nombre AS motivo, u.nombre AS vendedor,
                       count(DISTINCT m.id) AS documentos,
                       COALESCE(sum(d.cantidad_base), 0) AS unidades
                  FROM mermas m
                  JOIN merma_detalle d ON d.merma_id = m.id
                  JOIN productos p ON p.id = d.producto_id
                  JOIN motivos_merma mm ON mm.codigo = m.motivo_codigo
                  LEFT JOIN clientes c ON c.id = m.cliente_id
                  JOIN usuarios u ON u.id = m.vendedor_id
                 WHERE m.estado = 'confirmada'
                   AND m.tipo = 'cambio'
                   AND m.fecha_operativa BETWEEN :desde AND :hasta
                 GROUP BY c.nombre_comercial, p.nombre, mm.nombre, u.nombre
                 ORDER BY unidades DESC, c.nombre_comercial
                 LIMIT 200
                """
            ),
            {"desde": inicio, "hasta": fin},
        )
    ).mappings().all()

    visitas = sum(f["visitas"] for f in por_vendedor)
    ventas = sum(f["ventas"] for f in por_vendedor)
    nuestras = sum(f["cuantas"] for f in motivos if f["categoria"] in NUESTRA_CULPA)
    perdidas = visitas - ventas

    # `perdidas` cuenta CLIENTES-DÍA sin venta; `con_motivo` cuenta DOCUMENTOS. Las
    # dos cifras son correctas y pueden diferir cuando se pasó dos veces por el mismo
    # negocio el mismo día —a la segunda compró, o no compró ninguna de las dos—, y
    # la pantalla lo explica en vez de esconderlo: un total que no cuadra con su
    # desglose sin explicación destruye la confianza en todo el reporte.
    #
    # Lo que NO puede pasar es que haya no-drops sin motivo: `motivo_codigo` es llave
    # foránea a `motivos_no_drop`, así que el `JOIN` nunca pierde renglones.
    con_motivo = sum(f["cuantas"] for f in motivos)

    por_categoria: dict[str, int] = {}
    for f in motivos:
        por_categoria[f["categoria"]] = por_categoria.get(f["categoria"], 0) + f["cuantas"]

    return render(
        peticion,
        "efectividad.html",
        {
            "desde": inicio.isoformat(),
            "hasta": fin.isoformat(),
            "ruta": ruta,
            "rutas": (
                await sesion.execute(
                    text("SELECT codigo, nombre FROM rutas WHERE activo ORDER BY codigo")
                )
            ).mappings().all(),
            "por_vendedor": [
                dict(f, efectividad=_porcentaje(f["ventas"], f["visitas"]))
                for f in por_vendedor
            ],
            "motivos": motivos,
            "mermas": mermas,
            "cambios": cambios,
            "visitas": visitas,
            "ventas": ventas,
            "perdidas": perdidas,
            "con_motivo": con_motivo,
            "nuestras": nuestras,
            "efectividad": _porcentaje(ventas, visitas),
            "arreglables": _porcentaje(nuestras, perdidas),
            "por_categoria": por_categoria,
            "etiqueta_categoria": ETIQUETA_CATEGORIA,
            "nuestra_culpa": NUESTRA_CULPA,
            "unidades_mermadas": sum(
                Decimal(f["unidades"]) for f in mermas
            ),
            "unidades_al_vendedor": sum(
                Decimal(f["unidades"]) for f in mermas if f["afecta_vendedor"]
            ),
        },
        actor=actor,
        seccion="Efectividad",
    )
