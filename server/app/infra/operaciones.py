"""Operaciones: todas juntas, y anular deshaciendo lo que movieron (ADR 0002 §119).

────────────────────────────────────────────────────────────────────────────
EL PEDIDO
────────────────────────────────────────────────────────────────────────────
«Quiero una parte en el dashboard donde aparezcan todas las operaciones juntas
para borrar lo que yo necesite cuando quiera.» Y al preguntarle qué es
«borrar»: ANULAR Y DESHACER. La mercancía regresa a donde estaba, el dinero sale
de las cifras, el teléfono del vendedor se entera, y la operación se queda en la
lista como «anulada», con quién, cuándo y por qué.

────────────────────────────────────────────────────────────────────────────
CÓMO SE DESHACE SIN BORRAR EL LIBRO MAYOR
────────────────────────────────────────────────────────────────────────────
`movimientos_inventario` es append-only. Lo que deshace un movimiento es el
mismo movimiento AL REVÉS: de donde llegó a donde salió, por la misma cantidad.
`_deshacer_movimientos` lee lo que escribió la operación y lo escribe invertido,
así que sirve igual para una merma, una carga, una entrada o una devolución a
bodega, y el inventario sigue cuadrando contra su propia historia.

Lo que toca un CAMIÓN tiene que llegarle al teléfono, que ya lo había aplicado
por su cuenta:

  · la venta y la carga tienen su propio delta (la venta cancelada, migración
    0031; la carga cancelada, que el teléfono deshace);
  · la merma, la devolución del cliente, la devolución a bodega y el ajuste
    viajan como un ajuste del camión de tipo «anulación» (migración 0059): el
    delta firmado, con el motivo en la nota, aplicado una sola vez.

La venta y la venta de mayoreo no se deshacen aquí: se cancelan con las mismas
funciones de sus pantallas, para que las dos cancelen igual.

────────────────────────────────────────────────────────────────────────────
SIN TOPES: LO QUE SE DEJA, Y LO QUE SE DEJA AVISANDO (ADR 0002 §126)
────────────────────────────────────────────────────────────────────────────
«Quiero tener poder absoluto de eliminar lo que yo quiera y que se anulen los
efectos… cargas, ventas, movimientos del almacén, cortes incluso», para probar
con el almacén y luego borrar lo de prueba. Así que casi todo se deja:

  · **el corte** se deshace: los ajustes que dejó en el camión regresan, lo que
    se le cargó al vendedor se condona, y la carga vuelve a «por cortar» (el
    corte que mandó el vendedor, a «por cerrar»);
  · **la carga que ya entró a un corte** se elimina con su corte, en cascada;
  · **la compra ya pagada** se elimina con sus pagos;
  · **lo que dejaría un almacén en negativo** se deja si se marca la casilla:
    si ya se vendió o ya salió, el número queda abajo de cero y se corrige con
    un conteo. Sin la casilla no, porque casi siempre es un aviso de que falta
    eliminar primero lo que se hizo con esa mercancía.

Lo único que no se deja: anular dos veces, algo cuyo movimiento no está en el
inventario, y una venta a crédito ya cobrada (solo existen del piloto).

Se deja, pero hay que confirmarlo: una operación del camión de un día cuyo corte
ya se cerró. El corte firmado NO cambia —lo contado y lo entregado quedan como
se firmaron—, y eso es justo lo que la casilla dice antes de anular. Si se
quiere que cambie, se elimina también el corte.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.admin import ventas as panel_ventas
from app.api.admin.comun import auditar, dinero, sin_decimales
from app.infra import mayoreo
from app.infra.ajuste_de_existencia import (
    UnaSolaOperacion,
    existencia_con_candado,
    folio_del_ajuste,
)

# El orden del filtro: lo de la calle primero, lo de la oficina después.
TIPOS: tuple[tuple[str, str], ...] = (
    ("venta", "Venta"),
    ("mayoreo", "Venta de mayoreo"),
    ("no_venta", "Visita sin venta"),
    ("merma", "Merma"),
    ("devolucion", "Devolución del cliente"),
    ("cambio", "Cambio físico"),
    ("carga", "Carga"),
    ("devolucion_bodega", "Devolución a bodega"),
    ("traspaso", "Traspaso entre bodegas"),
    ("entrada", "Entrada a bodega"),
    ("salida", "Salida de bodega"),
    ("ajuste", "Ajuste de inventario"),
    ("corte", "Corte del día"),
)
NOMBRE = dict(TIPOS)

# Por omisión la lista enseña lo que sigue vivo: lo eliminado se va a su
# propio filtro (§126), y «Todas» es una opción más.
ESTADOS = (("vivas", "Sin eliminar"), ("anuladas", "Eliminadas"), ("todas", "Todas"))

# Los estados que ya no cuentan, venga de aquí o de su propia pantalla.
CANCELADOS = ("cancelada", "cancelado", "rechazado", "rechazada")

# Las que se pueden anular aquí: todas, el corte también (§126).
ANULABLES = tuple(t for t, _ in TIPOS)

# El orden en que se eliminan varias a la vez: primero lo que cuelga de otra
# cosa —la venta de su carga, el ajuste de su camión—, y al final de lo que
# cuelga lo demás —la carga de la bodega, la entrada—. Así no se queda nada en
# negativo a la mitad.
ORDEN_AL_ELIMINAR = (
    "venta", "mayoreo", "no_venta", "merma", "devolucion", "cambio", "ajuste",
    "devolucion_bodega", "corte", "carga", "traspaso", "salida", "entrada",
)

# Más renglones ya no se leen; para eso están los filtros.
LIMITE = 500

# Almacenes que son cuentas y no anaqueles: lo que se deshace puede dejarlos
# abajo de cero sin que nada físico falte (la merma ya se tiró; lo que va en
# tránsito tiene su diferencia con nombre).
SIN_REVISAR_EXISTENCIA = ("merma", "transito")

DIA_PASADO = date(1900, 1, 1)
DIA_FUTURO = date(9999, 12, 31)


class AnulacionRechazada(Exception):
    """No se puede anular así; el mensaje dice por qué y qué hacer."""


class OperacionNoExiste(Exception):
    """No hay una operación de ese tipo con ese id."""


# ---------------------------------------------------------------------------
# La lista
# ---------------------------------------------------------------------------

SQL_OPERACIONES = """
WITH ops AS (
    SELECT v.fecha_dispositivo AS momento, v.fecha_operativa AS fecha,
           'venta'::text AS tipo, v.id AS ref, v.folio_local AS folio,
           v.vendedor_id AS persona_id, c.nombre_comercial AS donde,
           CASE WHEN v.forma_pago = 'transferencia' THEN 'Por transferencia'
                ELSE 'En efectivo' END
             || ' · ' || COALESCE((SELECT trim_scale(sum(p.cantidad_base))::text
                                     FROM venta_partidas p WHERE p.venta_id = v.id), '0')
             || ' pza' AS detalle,
           v.total AS importe, v.estado, '/panel/ventas/' || v.id AS enlace
      FROM ventas v
      LEFT JOIN clientes c ON c.id = v.cliente_id
     WHERE v.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT m.creada_en, m.fecha, 'mayoreo', m.id, m.folio, m.vendida_por,
           c.nombre_comercial,
           'Desde ' || a.nombre || ' · '
             || COALESCE((SELECT trim_scale(sum(p.cantidad))::text
                            FROM venta_mayoreo_partidas p WHERE p.venta_id = m.id), '0')
             || ' pza',
           m.total, m.estado, '/panel/mayoreo/' || m.id
      FROM ventas_mayoreo m
      LEFT JOIN clientes c ON c.id = m.cliente_id
      LEFT JOIN almacenes a ON a.id = m.almacen_id
     WHERE m.fecha BETWEEN :desde AND :hasta

    UNION ALL
    SELECT n.fecha_dispositivo, n.fecha_operativa, 'no_venta', n.id,
           n.folio_consecutivo::text, n.vendedor_id, c.nombre_comercial,
           COALESCE(md.nombre, n.motivo_codigo)
             || COALESCE(' · ' || NULLIF(btrim(n.nota), ''), ''),
           NULL, 'confirmada', '/panel/clientes/' || n.cliente_id
      FROM no_drops n
      LEFT JOIN clientes c ON c.id = n.cliente_id
      LEFT JOIN motivos_no_drop md ON md.codigo = n.motivo_codigo
     WHERE n.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    -- La visita sin venta anulada ya no existe: lo que queda es su anulación.
    SELECT a.anulada_en, a.fecha_operativa, 'no_venta', a.documento_id, a.folio,
           a.vendedor_id, NULL, a.descripcion, NULL, 'anulada', NULL
      FROM anulaciones a
     WHERE a.tipo = 'no_venta' AND a.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT m.fecha_dispositivo, m.fecha_operativa,
           CASE m.tipo WHEN 'devolucion_cliente' THEN 'devolucion' ELSE m.tipo END,
           m.id, m.folio_local, m.vendedor_id, COALESCE(c.nombre_comercial, al.nombre),
           COALESCE(mm.nombre, m.motivo_codigo) || ' · '
             || COALESCE((SELECT trim_scale(sum(d.cantidad_base))::text
                            FROM merma_detalle d WHERE d.merma_id = m.id), '0')
             || ' pza',
           NULL, m.estado, NULL
      FROM mermas m
      LEFT JOIN clientes c ON c.id = m.cliente_id
      LEFT JOIN almacenes al ON al.id = m.almacen_id
      LEFT JOIN motivos_merma mm ON mm.codigo = m.motivo_codigo
     WHERE m.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(g.confirmada_en, g.creado_en), g.fecha_operativa, 'carga', g.id,
           g.folio, g.vendedor_id, d.nombre,
           'Desde ' || o.nombre || ' · '
             || COALESCE((SELECT trim_scale(sum(x.cantidad))::text
                            FROM carga_detalle x WHERE x.carga_id = g.id), '0')
             || ' pza',
           NULL, g.estado, '/panel/cargas/' || g.id
      FROM cargas g
      JOIN almacenes o ON o.id = g.almacen_origen_id
      JOIN almacenes d ON d.id = g.almacen_destino_id
     WHERE g.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(t.fecha_dispositivo, t.creado_en),
           COALESCE(t.fecha_operativa, t.creado_en::date),
           CASE WHEN o.tipo = 'camion' THEN 'devolucion_bodega' ELSE 'traspaso' END,
           t.id, t.folio, t.solicitado_por, o.nombre || ' → ' || d.nombre,
           COALESCE((SELECT trim_scale(sum(x.cantidad))::text
                       FROM traspaso_detalle x WHERE x.traspaso_id = t.id), '0')
             || ' pza' || COALESCE(' · ' || NULLIF(btrim(t.observaciones), ''), ''),
           NULL, t.estado,
           CASE WHEN o.tipo = 'camion' THEN '/panel/entradas/devolucion/' || t.id END
      FROM traspasos t
      JOIN almacenes o ON o.id = t.almacen_origen_id
      JOIN almacenes d ON d.id = t.almacen_destino_id
     WHERE COALESCE(t.fecha_operativa, t.creado_en::date) BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(e.confirmada_en, e.creado_en), e.fecha_operativa, 'entrada', e.id,
           e.folio, e.creado_por, a.nombre,
           CASE e.motivo WHEN 'compra' THEN 'Compra'
                         WHEN 'inicial' THEN 'Inventario inicial'
                         ELSE 'Sobrante de conteo' END
             || COALESCE(' · ' || pr.nombre, ' · ' || NULLIF(btrim(e.proveedor), ''), '')
             || ' · ' || COALESCE((SELECT trim_scale(sum(x.cantidad))::text
                                     FROM entrada_detalle x WHERE x.entrada_id = e.id), '0')
             || ' pza',
           e.importe_total, e.estado, '/panel/entradas/' || e.id
      FROM entradas e
      JOIN almacenes a ON a.id = e.almacen_destino_id
      LEFT JOIN proveedores pr ON pr.id = e.proveedor_id
     WHERE e.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(s.confirmada_en, s.creado_en), s.fecha_operativa, 'salida', s.id,
           s.folio, s.creado_por, a.nombre,
           CASE s.tipo WHEN 'merma' THEN 'Merma' ELSE 'Faltante de conteo' END
             || COALESCE(' · ' || mm.nombre, '')
             || ' · ' || COALESCE((SELECT trim_scale(sum(x.cantidad))::text
                                     FROM salida_detalle x WHERE x.salida_id = s.id), '0')
             || ' pza',
           NULL, s.estado, '/panel/salidas/' || s.id
      FROM salidas s
      JOIN almacenes a ON a.id = s.almacen_origen_id
      LEFT JOIN motivos_merma mm ON mm.codigo = s.motivo_codigo
     WHERE s.fecha_operativa BETWEEN :desde AND :hasta

    UNION ALL
    -- Los ajustes de tipo «anulación» no son operaciones: son lo que dejó una
    -- anulación, y anularlos sería deshacer el deshacer. Una captura de varios
    -- artículos —el conteo, mover mercancía— es UNA operación (§146): se ve su
    -- cabeza, y los demás ajustes van con ella (`pareja_id`). Lo que suma `g`
    -- es lo del almacén de la cabeza: de un traspaso, lo que salió.
    SELECT j.creado_en, j.creado_en::date, 'ajuste', j.id, j.folio,
           COALESCE(a.responsable_id, j.usuario_id), a.nombre,
           CASE WHEN g.articulos = 1
                THEN p.nombre || ' ' || CASE WHEN j.delta > 0 THEN '+' ELSE '' END
                       || trim_scale(j.delta)::text
                WHEN j.tipo = 'traspaso'
                THEN g.articulos || ' artículos, ' || trim_scale(g.bajan)::text || ' pza'
                ELSE g.articulos || ' artículos'
                       || COALESCE(', ' || trim_scale(g.suben)::text || ' pza de más', '')
                       || COALESCE(', ' || trim_scale(g.bajan)::text || ' de menos', '')
           END || ' · ' || j.nota,
           NULL, 'confirmada',
           CASE WHEN g.articulos = 1
                THEN '/panel/inventario/' || j.almacen_id || '/' || j.producto_id END
      FROM ajustes_camion j
      JOIN almacenes a ON a.id = j.almacen_id
      JOIN productos p ON p.id = j.producto_id
     CROSS JOIN LATERAL (
           SELECT count(DISTINCT x.producto_id) AS articulos,
                  sum(x.delta) FILTER (WHERE x.almacen_id = j.almacen_id AND x.delta > 0)
                    AS suben,
                  -sum(x.delta) FILTER (WHERE x.almacen_id = j.almacen_id AND x.delta < 0)
                    AS bajan
             FROM ajustes_camion x
            WHERE x.id = j.id OR x.pareja_id = j.id
     ) g
     WHERE j.tipo <> 'anulacion' AND j.pareja_id IS NULL
       AND j.creado_en::date BETWEEN :desde AND :hasta

    UNION ALL
    SELECT COALESCE(l.cerrada_en, l.creado_en), l.fecha_operativa, 'corte', l.id, l.folio,
           l.vendedor_id, NULL,
           CASE WHEN COALESCE(l.diferencia_efectivo, 0) <> 0
                THEN 'Con diferencia de efectivo' ELSE 'Corte del día' END,
           l.efectivo_entregado, l.estado, '/panel/liquidaciones/' || l.id
      FROM liquidaciones l
     WHERE l.fecha_operativa BETWEEN :desde AND :hasta
)
SELECT o.*, u.nombre AS persona, u.codigo AS persona_codigo,
       an.id AS anulacion_id, an.motivo AS anulacion_motivo,
       an.anulada_en, q.nombre AS anulada_por, an.datos AS anulacion_datos
  FROM ops o
  LEFT JOIN usuarios u ON u.id = o.persona_id
  LEFT JOIN anulaciones an ON an.tipo = o.tipo AND an.documento_id = o.ref
  LEFT JOIN usuarios q ON q.id = an.usuario_id
 WHERE (:tipo = '' OR o.tipo = :tipo)
   AND (CAST(:persona AS uuid) IS NULL OR o.persona_id = CAST(:persona AS uuid))
   AND (CAST(:ref AS uuid) IS NULL OR o.ref = CAST(:ref AS uuid))
   AND (:estado = ''
        OR (:estado = 'anuladas' AND an.id IS NOT NULL)
        OR (:estado = 'vivas' AND an.id IS NULL
            AND o.estado NOT IN ('cancelada', 'cancelado', 'rechazado', 'rechazada')))
 ORDER BY o.fecha DESC, o.momento DESC NULLS LAST, o.folio DESC
 LIMIT :limite
"""


@dataclass
class Operacion:
    tipo: str
    ref: uuid.UUID
    folio: str | None
    momento: datetime | None
    fecha: date
    persona_id: uuid.UUID | None
    persona: str | None
    donde: str | None
    detalle: str | None
    importe: Decimal | None
    estado: str
    enlace: str | None
    anulacion_motivo: str | None = None
    anulada_en: datetime | None = None
    anulada_por: str | None = None
    anulacion_datos: dict = field(default_factory=dict)

    @property
    def nombre(self) -> str:
        return NOMBRE[self.tipo]

    @property
    def anulada(self) -> bool:
        return self.anulada_en is not None

    @property
    def cancelada(self) -> bool:
        return self.estado in CANCELADOS

    @property
    def estado_texto(self) -> str:
        if self.anulada:
            return "anulada"
        return self.estado.replace("_", " ")

    @property
    def anulable(self) -> bool:
        """Si vale la pena ofrecer «Anular». Lo que de verdad lo impide se revisa
        al abrirla (`revisar`): es caro por renglón."""
        return self.tipo in ANULABLES and not self.anulada and not self.cancelada

    @property
    def pagina(self) -> str:
        return f"/panel/operaciones/{self.tipo}/{self.ref}"


def _operacion(fila) -> Operacion:
    return Operacion(
        tipo=fila["tipo"],
        ref=fila["ref"],
        folio=fila["folio"],
        momento=fila["momento"],
        fecha=fila["fecha"],
        persona_id=fila["persona_id"],
        persona=fila["persona"],
        donde=fila["donde"],
        detalle=fila["detalle"],
        importe=fila["importe"],
        estado=fila["estado"] or "",
        enlace=fila["enlace"],
        anulacion_motivo=fila["anulacion_motivo"],
        anulada_en=fila["anulada_en"],
        anulada_por=fila["anulada_por"],
        anulacion_datos=fila["anulacion_datos"] or {},
    )


async def listar(
    sesion,
    *,
    desde: date,
    hasta: date,
    tipo: str = "",
    persona: uuid.UUID | None = None,
    estado: str = "",
    limite: int = LIMITE,
) -> list[Operacion]:
    filas = (
        await sesion.execute(
            text(SQL_OPERACIONES),
            {
                "desde": desde,
                "hasta": hasta,
                "tipo": tipo if tipo in NOMBRE else "",
                "persona": persona,
                "ref": None,
                # «Todas» es sin filtro.
                "estado": estado if estado in ("vivas", "anuladas") else "",
                "limite": limite,
            },
        )
    ).mappings().all()
    return [_operacion(f) for f in filas]


async def una(sesion, tipo: str, ref: uuid.UUID) -> Operacion | None:
    if tipo not in NOMBRE:
        return None
    fila = (
        await sesion.execute(
            text(SQL_OPERACIONES),
            {
                "desde": DIA_PASADO,
                "hasta": DIA_FUTURO,
                "tipo": tipo,
                "persona": None,
                "ref": ref,
                "estado": "",
                "limite": 1,
            },
        )
    ).mappings().first()
    return _operacion(fila) if fila is not None else None


async def personas(sesion) -> list[dict]:
    """Quién puede aparecer en la lista: los vendedores y quien captura en la
    oficina. Todos, aunque estén desactivados: su historia sigue ahí."""
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text("SELECT id, codigo, nombre, activo FROM usuarios ORDER BY nombre")
            )
        ).mappings().all()
    ]


# ---------------------------------------------------------------------------
# Lo que va a pasar
# ---------------------------------------------------------------------------


@dataclass
class Renglon:
    producto: str
    cantidad: Decimal
    de: str
    a: str


@dataclass
class Plan:
    """Lo que anular va a hacer, lo que lo impide y lo que hay que confirmar."""

    efectos: list[str] = field(default_factory=list)
    bloqueos: list[str] = field(default_factory=list)
    # Lo que quedaría abajo de cero. No bloquea: pide marcar la casilla (§126).
    negativos: list[str] = field(default_factory=list)
    renglones: list[Renglon] = field(default_factory=list)
    # El folio del corte ya cerrado de ese día: no cambia, y hay que decir que
    # se entiende antes de anular.
    corte_cerrado: str | None = None


# El documento con el que cada operación escribió el libro mayor.
DOCUMENTO_DEL_LIBRO = {
    "merma": "merma",
    "devolucion": "merma",
    "cambio": "merma",
    "carga": "carga",
    "devolucion_bodega": "traspaso",
    "traspaso": "traspaso",
    "entrada": "entrada",
    "salida": "salida",
    "ajuste": "ajuste_camion",
}


async def revisar(sesion, op: Operacion, *, candado: bool = False) -> Plan:
    """Lo que pasaría al anular `op`, sin escribir nada.

    Con `candado` bloquea las existencias que va a revisar: es como lo llama
    `anular`, para que entre revisar y escribir no se vaya la mercancía.
    """
    plan = Plan()
    if op.anulada:
        cuando = op.anulada_en.strftime("%d/%m/%Y %H:%M") if op.anulada_en else ""
        plan.bloqueos.append(
            f"Ya se anuló el {cuando} ({op.anulada_por or 'alguien'}): {op.anulacion_motivo}"
        )
        return plan
    if op.cancelada:
        plan.bloqueos.append(
            f"Ya está {op.estado}: no movió nada que deshacer, o ya se deshizo."
        )
        return plan

    revisor = _REVISORES[op.tipo]
    await revisor(sesion, op, plan, candado)
    return plan


async def _revisar_venta(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    cab = await panel_ventas._cabecera(sesion, op.ref)
    pagado = cab["importe_pagado"]
    if pagado is not None and Decimal(pagado) > 0:
        plan.bloqueos.append(
            f"Esta venta a crédito ya tiene {dinero(pagado)} cobrados y aplicados. "
            "Reversa el cobro primero: si no, quedaría un pago aplicado a una "
            "factura que ya no existe."
        )
    partidas = (
        await sesion.execute(
            text(
                "SELECT p.nombre, sum(vp.cantidad_base) AS cantidad "
                "  FROM venta_partidas vp JOIN productos p ON p.id = vp.producto_id "
                " WHERE vp.venta_id = :v GROUP BY p.nombre ORDER BY p.nombre"
            ),
            {"v": op.ref},
        )
    ).mappings().all()
    plan.renglones = [
        Renglon(f["nombre"], Decimal(f["cantidad"]), "el cliente", cab["camion"])
        for f in partidas
    ]
    piezas = sum((r.cantidad for r in plan.renglones), Decimal(0))
    plan.efectos.append(
        f"La venta pasa a cancelada y sus {sin_decimales(piezas)} piezas vuelven a "
        f"{cab['camion']}."
    )
    efectivo = cab["tipo"] == "contado" and cab["forma_pago"] == "efectivo"
    plan.efectos.append(
        f"Sale de las ventas del {op.fecha:%d/%m} ({dinero(cab['total'])})"
        + (" y del efectivo que el vendedor tiene que entregar." if efectivo else ".")
    )
    if cab["tipo"] == "credito":
        plan.efectos.append("La deuda del cliente por esta venta se borra.")
    plan.efectos.append(
        f"El teléfono de {cab['vendedor']} la ve cancelada, con tu motivo, en su "
        "siguiente sincronización."
    )
    plan.corte_cerrado = await _corte_cerrado(
        sesion, cab["vendedor_id"], cab["fecha_operativa"], carga=cab["carga_id"]
    )


async def _revisar_mayoreo(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    filas = (
        await sesion.execute(
            text(
                "SELECT p.nombre, sum(x.cantidad) AS cantidad, a.nombre AS bodega "
                "  FROM venta_mayoreo_partidas x "
                "  JOIN ventas_mayoreo v ON v.id = x.venta_id "
                "  JOIN almacenes a ON a.id = v.almacen_id "
                "  JOIN productos p ON p.id = x.producto_id "
                " WHERE x.venta_id = :v GROUP BY p.nombre, a.nombre ORDER BY p.nombre"
            ),
            {"v": op.ref},
        )
    ).mappings().all()
    plan.renglones = [
        Renglon(f["nombre"], Decimal(f["cantidad"]), "el cliente", f["bodega"]) for f in filas
    ]
    bodega = filas[0]["bodega"] if filas else "su bodega"
    plan.efectos.append(
        f"La venta de mayoreo pasa a cancelada y la mercancía vuelve a {bodega}."
    )
    plan.efectos.append(
        f"Sale de las ventas de mayoreo del {op.fecha:%d/%m} ({dinero(op.importe)})."
    )


async def _revisar_no_venta(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    reprogramada = (
        await sesion.execute(
            text("SELECT reprogramada_para FROM no_drops WHERE id = :n"), {"n": op.ref}
        )
    ).scalar_one_or_none()
    plan.efectos.append(
        "Se borra la visita sin venta: deja de contar en las visitas, la "
        "efectividad y el desempeño del día. Aquí queda anotada como anulada."
    )
    if reprogramada is not None:
        plan.efectos.append(
            f"La visita que se reprogramó para el {reprogramada:%d/%m} se quita: el "
            "cliente vuelve a su día de siempre."
        )
    plan.efectos.append(
        "No movió mercancía ni dinero. En el teléfono del vendedor se queda en el "
        "historial de su día."
    )


async def _revisar_merma(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    merma = (
        await sesion.execute(
            text(
                "SELECT m.vendedor_id, m.fecha_operativa, m.almacen_id, a.nombre AS camion, "
                "       u.nombre AS vendedor, mm.afecta_vendedor "
                "  FROM mermas m JOIN almacenes a ON a.id = m.almacen_id "
                "  JOIN usuarios u ON u.id = m.vendedor_id "
                "  LEFT JOIN motivos_merma mm ON mm.codigo = m.motivo_codigo "
                " WHERE m.id = :m"
            ),
            {"m": op.ref},
        )
    ).mappings().one()
    movimientos = await _movimientos(sesion, "merma", op.ref)
    await _llenar_movimientos(sesion, plan, movimientos, candado)
    if op.tipo == "devolucion":
        plan.efectos.append(
            f"Las piezas salen de {merma['camion']}: el cliente no las devolvió."
        )
    else:
        plan.efectos.append(
            f"Las piezas regresan a {merma['camion']}"
            + (" y salen del almacén de merma." if _toca_merma(movimientos) else ".")
        )
        plan.efectos.append(
            "Deja de contar en las mermas del día"
            + (" y en lo que se le cobra al vendedor." if merma["afecta_vendedor"] else ".")
        )
    plan.efectos.append(
        f"El teléfono de {merma['vendedor']} recibe un ajuste del camión con tu "
        "motivo y lo suma solo en su siguiente sincronización."
    )
    plan.corte_cerrado = await _corte_cerrado(
        sesion, merma["vendedor_id"], merma["fecha_operativa"]
    )


async def _revisar_carga(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    carga = (
        await sesion.execute(
            text(
                "SELECT g.estado, o.nombre AS bodega, d.nombre AS camion, u.nombre AS vendedor "
                "  FROM cargas g JOIN almacenes o ON o.id = g.almacen_origen_id "
                "  JOIN almacenes d ON d.id = g.almacen_destino_id "
                "  LEFT JOIN usuarios u ON u.id = g.vendedor_id "
                " WHERE g.id = :c"
            ),
            {"c": op.ref},
        )
    ).mappings().one()
    if carga["estado"] == "borrador":
        plan.efectos.append("Es un borrador: no ha movido nada. Se cancela y ya.")
        return
    movimientos = await _movimientos(sesion, "carga", op.ref)
    # Su corte se va con ella (§126): primero se deshace el corte y después la
    # carga, y el inventario se revisa con los dos juntos.
    for corte in await _cortes_vivos(sesion, op.ref):
        plan.efectos.append(f"Se elimina también su corte {corte['folio']}:")
        movimientos += await _efectos_del_corte(sesion, corte, plan, reabrir=False)
    del_vendedor = (
        await sesion.execute(
            text(
                "SELECT count(*) FROM cortes_vendedor "
                " WHERE carga_id = :c AND estado IN ('pendiente', 'cerrado')"
            ),
            {"c": op.ref},
        )
    ).scalar_one()
    if del_vendedor:
        plan.efectos.append(
            "El corte que mandó el vendedor con esta carga se elimina: sale de "
            "«Cortes por cerrar»."
        )
    solicitud = (
        await sesion.execute(
            text("SELECT 1 FROM solicitudes_carga WHERE carga_id = :c AND estado = 'aceptada'"),
            {"c": op.ref},
        )
    ).first()
    if solicitud is not None:
        plan.efectos.append(
            "La carga que pidió el vendedor queda rechazada con tu motivo: lo ve en su "
            "«Mi día» y puede pedir otra para ese día."
        )
    await _llenar_movimientos(sesion, plan, movimientos, candado)
    plan.efectos.append(
        f"La mercancía regresa de {carga['camion']} a {carga['bodega']}."
    )
    plan.efectos.append(
        f"El teléfono de {carga['vendedor'] or 'su vendedor'} deshace la carga en su "
        "siguiente sincronización. Ese día se le puede hacer otra carga."
    )


async def _revisar_traspaso(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    t = (
        await sesion.execute(
            text(
                "SELECT t.estado, o.nombre AS origen, d.nombre AS destino, "
                "       u.nombre AS quien "
                "  FROM traspasos t JOIN almacenes o ON o.id = t.almacen_origen_id "
                "  JOIN almacenes d ON d.id = t.almacen_destino_id "
                "  LEFT JOIN usuarios u ON u.id = t.solicitado_por "
                " WHERE t.id = :t"
            ),
            {"t": op.ref},
        )
    ).mappings().one()
    await _llenar_movimientos(
        sesion, plan, await _movimientos(sesion, "traspaso", op.ref), candado
    )
    if op.tipo == "devolucion_bodega":
        plan.efectos.append(
            f"La mercancía regresa a {t['origen']}"
            + (": sale de la bodega lo que se recibió y de tránsito lo que faltó."
               if t["estado"] == "aceptado" else ", desde tránsito.")
        )
        plan.efectos.append(
            f"El teléfono de {t['quien'] or 'su vendedor'} la ve cancelada y recibe un "
            "ajuste del camión con tu motivo en su siguiente sincronización."
        )
    else:
        plan.efectos.append(f"La mercancía regresa de {t['destino']} a {t['origen']}.")


async def _revisar_entrada(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    if op.estado == "borrador":
        plan.efectos.append("Es un borrador: no ha movido nada. Se cancela y ya.")
        return
    await _llenar_movimientos(
        sesion, plan, await _movimientos(sesion, "entrada", op.ref), candado
    )
    cuenta = (
        await sesion.execute(
            text(
                "SELECT c.importe_original, p.nombre AS proveedor, "
                "       (SELECT COALESCE(sum(importe), 0) FROM pagos_proveedor "
                "         WHERE entrada_id = c.entrada_id) AS pagado "
                "  FROM cuentas_por_pagar c JOIN proveedores p ON p.id = c.proveedor_id "
                " WHERE c.entrada_id = :e"
            ),
            {"e": op.ref},
        )
    ).mappings().first()
    plan.efectos.append(f"La mercancía sale de {op.donde}: la entrada no ocurrió.")
    if cuenta is not None:
        plan.efectos.append(
            f"Se borra la cuenta por pagar de {dinero(cuenta['importe_original'])} "
            f"a {cuenta['proveedor']}."
        )
        if Decimal(cuenta["pagado"]) > 0:
            # Con la cuenta se van sus pagos (ON DELETE CASCADE, migración 0027).
            plan.efectos.append(
                f"Y con ella los {dinero(cuenta['pagado'])} que ya se le habían pagado: "
                "si de verdad se pagaron, se registran otra vez en la compra correcta."
            )
    plan.efectos.append(
        "El costo promedio de los productos no se recalcula: queda como lo dejó "
        "esta entrada."
    )


async def _revisar_salida(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    if op.estado == "borrador":
        plan.efectos.append("Es un borrador: no ha movido nada. Se cancela y ya.")
        return
    await _llenar_movimientos(
        sesion, plan, await _movimientos(sesion, "salida", op.ref), candado
    )
    plan.efectos.append(f"La mercancía regresa a {op.donde}: la salida no ocurrió.")


async def _revisar_ajuste(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    # Con todos los de su operación (§146): los artículos de un conteo, las dos
    # mitades de cada artículo de un traspaso bodega↔camión (§135).
    ajustes = (
        await sesion.execute(
            text(
                "SELECT j.tipo, j.delta, j.producto_id, j.almacen_id, a.nombre AS almacen, "
                "       a.tipo AS almacen_tipo, u.nombre AS responsable "
                "  FROM ajustes_camion j JOIN almacenes a ON a.id = j.almacen_id "
                "  LEFT JOIN usuarios u ON u.id = a.responsable_id "
                " WHERE j.id = :j OR j.pareja_id = :j"
                " ORDER BY j.pareja_id NULLS FIRST, j.creado_en"
            ),
            {"j": op.ref},
        )
    ).mappings().all()
    await _llenar_movimientos(sesion, plan, await _movimientos_del_ajuste(sesion, op.ref), candado)
    articulos = len({a["producto_id"] for a in ajustes})
    sale = next((a for a in ajustes if a["delta"] < 0), None)
    entra = next((a for a in ajustes if a["delta"] > 0), None)
    if ajustes and ajustes[0]["tipo"] == "traspaso" and sale and entra:
        plan.efectos.append(
            f"La mercancía regresa de {entra['almacen']} a {sale['almacen']}: "
            "el traspaso no ocurrió."
        )
    elif articulos > 1:
        plan.efectos.append(
            f"Se escribe el ajuste contrario de los {articulos} artículos en {op.donde}: "
            "la operación se deshace entera."
        )
    else:
        plan.efectos.append(f"Se escribe el ajuste contrario en {op.donde}.")
    # Una vez por camión, no una por artículo.
    camiones = {a["almacen_id"]: a for a in ajustes if a["almacen_tipo"] == "camion"}
    for ajuste in camiones.values():
        plan.efectos.append(
            f"El teléfono de {ajuste['responsable'] or 'su vendedor'} lo recibe con tu "
            "motivo en su siguiente sincronización."
        )


async def _movimientos_del_ajuste(sesion, ajuste_id: uuid.UUID) -> list[dict]:
    """Lo que movió el ajuste y, si es la cabeza de una operación de varios
    (§146), lo que movieron los demás: se anulan juntos."""
    movimientos = await _movimientos(sesion, "ajuste_camion", ajuste_id)
    for otro in (
        await sesion.execute(
            text("SELECT id FROM ajustes_camion WHERE pareja_id = :j ORDER BY creado_en"),
            {"j": ajuste_id},
        )
    ).scalars():
        movimientos += await _movimientos(sesion, "ajuste_camion", otro)
    return movimientos


async def _revisar_corte(sesion, op: Operacion, plan: Plan, candado: bool) -> None:
    corte = await _corte(sesion, op.ref)
    movimientos = await _efectos_del_corte(sesion, corte, plan, reabrir=True)
    if movimientos:
        await _llenar_movimientos(sesion, plan, movimientos, candado)


async def _corte(sesion, liquidacion_id: uuid.UUID) -> dict:
    """El corte con lo que hace falta para deshacerlo."""
    return dict(
        (
            await sesion.execute(
                text(
                    "SELECT l.id, l.folio, l.estado, l.carga_id, l.vendedor_id, "
                    "       l.fecha_operativa, g.folio AS carga, g.estado AS carga_estado, "
                    "       a.nombre AS camion, u.nombre AS vendedor "
                    "  FROM liquidaciones l JOIN cargas g ON g.id = l.carga_id "
                    "  JOIN almacenes a ON a.id = g.almacen_destino_id "
                    "  JOIN usuarios u ON u.id = l.vendedor_id "
                    " WHERE l.id = :l"
                ),
                {"l": liquidacion_id},
            )
        ).mappings().one()
    )


async def _cortes_vivos(sesion, carga_id: uuid.UUID) -> list[dict]:
    """Los cortes de la carga que no se han anulado: uno, o ninguno."""
    filas = (
        await sesion.execute(
            text("SELECT id FROM liquidaciones WHERE carga_id = :c AND estado <> 'anulada'"),
            {"c": carga_id},
        )
    ).scalars().all()
    return [await _corte(sesion, f) for f in filas]


async def _cargado_por_el_corte(sesion, liquidacion_id: uuid.UUID) -> Decimal:
    """Lo que el corte le cargó al vendedor y todavía no se le condona."""
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE(sum(importe) FILTER (WHERE tipo = 'cargo'), 0) "
                    "     - COALESCE(sum(importe) FILTER (WHERE tipo = 'condonacion'), 0) "
                    "  FROM cuenta_vendedor WHERE liquidacion_id = :l"
                ),
                {"l": liquidacion_id},
            )
        ).scalar_one()
    )


async def _efectos_del_corte(sesion, corte: dict, plan: Plan, *, reabrir: bool) -> list[dict]:
    """Lo que deshacer el corte va a hacer, en palabras. Devuelve sus movimientos
    —los ajustes que dejó en el camión— para revisarlos con los demás.

    `reabrir` es eliminar solo el corte: la carga vuelve a «por cortar» y el
    corte que mandó el vendedor a «por cerrar». Sin él, el corte se va porque se
    elimina su carga, y con ella todo lo demás."""
    if corte["estado"] != "cerrada":
        plan.efectos.append(
            f"El corte {corte['folio']} está abierto: se descarta lo que se haya "
            "contado y el arqueo."
        )
        movimientos: list[dict] = []
    else:
        movimientos = await _movimientos(sesion, "liquidacion", corte["id"])
        if movimientos:
            plan.efectos.append(
                f"Los ajustes que dejó el corte en {corte['camion']} se deshacen: el "
                "camión vuelve a lo que el sistema calculaba antes de cortarlo. El "
                f"teléfono de {corte['vendedor']} los recibe en su siguiente "
                "sincronización."
            )
        else:
            plan.efectos.append(f"El corte no dejó ajustes: {corte['camion']} no cambia.")
        cargado = await _cargado_por_el_corte(sesion, corte["id"])
        if cargado > 0:
            plan.efectos.append(
                f"Se le condonan a {corte['vendedor']} los {dinero(cargado)} que este "
                "corte le cargó (faltantes, mermas a su cargo, efectivo): su cuenta "
                "queda como antes."
            )
        plan.efectos.append(
            f"Deja de contar en los cortes y en el efectivo entregado del "
            f"{corte['fecha_operativa']:%d/%m}."
        )
    if reabrir:
        plan.efectos.append(f"La carga {corte['carga']} vuelve a «por cortar».")
        del_vendedor = (
            await sesion.execute(
                text("SELECT 1 FROM cortes_vendedor "
                     " WHERE liquidacion_id = :l AND estado = 'cerrado'"),
                {"l": corte["id"]},
            )
        ).first()
        if del_vendedor is not None:
            plan.efectos.append(
                f"El corte que mandó {corte['vendedor']} desde su teléfono vuelve a "
                "«Cortes por cerrar», para cerrarlo otra vez contando el efectivo."
            )
    return movimientos


_REVISORES = {
    "venta": _revisar_venta,
    "mayoreo": _revisar_mayoreo,
    "no_venta": _revisar_no_venta,
    "merma": _revisar_merma,
    "devolucion": _revisar_merma,
    "cambio": _revisar_merma,
    "carga": _revisar_carga,
    "devolucion_bodega": _revisar_traspaso,
    "traspaso": _revisar_traspaso,
    "entrada": _revisar_entrada,
    "salida": _revisar_salida,
    "ajuste": _revisar_ajuste,
    "corte": _revisar_corte,
}


# ---------------------------------------------------------------------------
# Los movimientos de la operación, y al revés
# ---------------------------------------------------------------------------


async def _movimientos(sesion, documento_tipo: str, documento_id: uuid.UUID) -> list[dict]:
    """Lo que la operación escribió en el libro mayor, con nombres para leerlo."""
    return [
        dict(f)
        for f in (
            await sesion.execute(
                text(
                    """
                    SELECT m.almacen_origen_id AS origen, m.almacen_destino_id AS destino,
                           m.producto_id, m.cantidad, m.lote,
                           o.nombre AS origen_nombre, o.tipo AS origen_tipo,
                           d.nombre AS destino_nombre, d.tipo AS destino_tipo,
                           p.nombre AS producto
                      FROM movimientos_inventario m
                      LEFT JOIN almacenes o ON o.id = m.almacen_origen_id
                      LEFT JOIN almacenes d ON d.id = m.almacen_destino_id
                      JOIN productos p ON p.id = m.producto_id
                     WHERE m.documento_tipo = :t AND m.documento_id = :d
                     ORDER BY p.nombre, m.fecha_servidor
                    """
                ),
                {"t": documento_tipo, "d": documento_id},
            )
        ).mappings().all()
    ]


def _neto(movimientos: list[dict]) -> dict[tuple, Decimal]:
    """Lo que DESHACER le cambia a cada almacén y producto: al revés de lo que
    hizo la operación (lo que entró, sale; lo que salió, entra)."""
    neto: dict[tuple, Decimal] = {}
    for m in movimientos:
        cantidad = Decimal(m["cantidad"])
        if m["destino"] is not None:
            clave = (m["destino"], m["producto_id"])
            neto[clave] = neto.get(clave, Decimal(0)) - cantidad
        if m["origen"] is not None:
            clave = (m["origen"], m["producto_id"])
            neto[clave] = neto.get(clave, Decimal(0)) + cantidad
    return neto


def _toca_merma(movimientos: list[dict]) -> bool:
    return any(m["destino_tipo"] == "merma" for m in movimientos)


async def _llenar_movimientos(sesion, plan: Plan, movimientos: list[dict], candado: bool) -> None:
    """Los renglones que se van a regresar, y lo que lo impide: dejar un almacén
    abajo de cero (§118). Si ya se vendió o ya salió, no hay qué regresar."""
    plan.renglones = [
        Renglon(
            m["producto"],
            Decimal(m["cantidad"]),
            m["destino_nombre"] or "fuera del sistema",
            m["origen_nombre"] or "fuera del sistema",
        )
        for m in movimientos
    ]
    if not movimientos:
        # Anular solo cambiaría el estado, y el teléfono deshará lo que crea que
        # se movió: el inventario del servidor y el del camión se separarían.
        plan.bloqueos.append(
            "No encontré lo que movió en el inventario, así que no hay qué "
            "regresar. Corrígelo con un ajuste de inventario."
        )
        return
    tipos ={m["origen"]: m["origen_tipo"] for m in movimientos} | {
        m["destino"]: m["destino_tipo"] for m in movimientos
    }
    nombres = {m["origen"]: m["origen_nombre"] for m in movimientos} | {
        m["destino"]: m["destino_nombre"] for m in movimientos
    }
    productos = {m["producto_id"]: m["producto"] for m in movimientos}
    for (almacen, producto), cambio in sorted(
        _neto(movimientos).items(), key=lambda par: (str(par[0][0]), productos[par[0][1]])
    ):
        if cambio >= 0 or tipos.get(almacen) in SIN_REVISAR_EXISTENCIA:
            continue
        hay = (
            await existencia_con_candado(sesion, almacen, producto)
            if candado
            else await _existencia(sesion, almacen, producto)
        )
        if hay + cambio < 0:
            plan.negativos.append(
                f"{nombres[almacen]} ya no tiene {sin_decimales(-cambio)} de "
                f"{productos[producto]} (hay {sin_decimales(hay)}): ya se vendió o ya "
                "salió, y anular la dejaría en negativo."
            )


async def _existencia(sesion, almacen, producto) -> Decimal:
    return Decimal(
        (
            await sesion.execute(
                text(
                    "SELECT COALESCE((SELECT cantidad FROM existencias "
                    " WHERE almacen_id = :a AND producto_id = :p), 0)"
                ),
                {"a": almacen, "p": producto},
            )
        ).scalar_one()
    )


async def _corte_cerrado(sesion, vendedor_id, dia, *, carga=None) -> str | None:
    """El folio del corte ya cerrado del vendedor ese día, si lo hay."""
    return (
        await sesion.execute(
            text(
                "SELECT folio FROM liquidaciones "
                " WHERE estado = 'cerrada' "
                "   AND ((CAST(:carga AS uuid) IS NOT NULL AND carga_id = CAST(:carga AS uuid)) "
                "        OR (vendedor_id = :v AND fecha_operativa = :d)) "
                " ORDER BY cerrada_en DESC NULLS LAST LIMIT 1"
            ),
            {"carga": carga, "v": vendedor_id, "d": dia},
        )
    ).scalar_one_or_none()


async def _deshacer_movimientos(
    sesion,
    movimientos: list[dict],
    *,
    anulacion_id: uuid.UUID,
    nota: str,
    quien: uuid.UUID,
    ahora: datetime,
    avisar_al_telefono: bool,
) -> list[str]:
    """Escribe cada movimiento al revés y mueve las existencias. Devuelve los
    folios de los ajustes del camión que le avisan al teléfono.

    Con `avisar_al_telefono`, lo que cambia en un camión se documenta con un
    ajuste de tipo «anulación» por producto (su delta viaja al teléfono), y el
    movimiento que toca el camión apunta a ese ajuste —como cualquier ajuste—.
    Lo demás apunta a la anulación.
    """
    ajustes: dict[tuple, uuid.UUID] = {}
    folios: list[str] = []
    # Los ajustes de la anulación también van juntos (§146): un solo folio.
    operacion = UnaSolaOperacion()
    if avisar_al_telefono:
        camiones = {m["origen"] for m in movimientos if m["origen_tipo"] == "camion"} | {
            m["destino"] for m in movimientos if m["destino_tipo"] == "camion"
        }
        for (almacen, producto), delta in _neto(movimientos).items():
            if almacen not in camiones or delta == 0:
                continue
            ajuste_id, folio = await _ajuste_de_anulacion(
                sesion, almacen, producto, delta, nota=nota, quien=quien, ahora=ahora,
                operacion=operacion,
            )
            ajustes[(almacen, producto)] = ajuste_id
            folios.append(folio)

    for m in movimientos:
        # Al revés: sale de donde llegó y entra a donde salió.
        origen, destino = m["destino"], m["origen"]
        documento_tipo, documento_id = "anulacion", anulacion_id
        for almacen in (origen, destino):
            if almacen is not None and (almacen, m["producto_id"]) in ajustes:
                documento_tipo = "ajuste_camion"
                documento_id = ajustes[(almacen, m["producto_id"])]
        await sesion.execute(
            text(
                """
                INSERT INTO movimientos_inventario
                  (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
                   lote, documento_tipo, documento_id, usuario_id, fecha_servidor)
                VALUES ('ajuste', :origen, :destino, :p, :cant, :lote, :dtipo, :doc,
                        :quien, :ahora)
                """
            ),
            {
                "origen": origen,
                "destino": destino,
                "p": m["producto_id"],
                "cant": m["cantidad"],
                "lote": m["lote"],
                "dtipo": documento_tipo,
                "doc": documento_id,
                "quien": quien,
                "ahora": ahora,
            },
        )
        cantidad = Decimal(m["cantidad"])
        for almacen, delta in ((origen, -cantidad), (destino, cantidad)):
            if almacen is None:
                continue
            await sesion.execute(
                text(
                    """
                    INSERT INTO existencias (almacen_id, producto_id, cantidad, actualizado_en)
                    VALUES (:a, :p, :delta, :ahora)
                    ON CONFLICT (almacen_id, producto_id) DO UPDATE
                       SET cantidad = existencias.cantidad + :delta, actualizado_en = :ahora
                    """
                ),
                {"a": almacen, "p": m["producto_id"], "delta": delta, "ahora": ahora},
            )
    return folios


async def _ajuste_de_anulacion(
    sesion, almacen, producto, delta: Decimal, *, nota: str, quien, ahora,
    operacion: UnaSolaOperacion,
) -> tuple[uuid.UUID, str]:
    """El ajuste del camión que le dice al teléfono «súmale esto». Su disparador
    (migración 0032) publica el delta al dueño del camión."""
    actual = await existencia_con_candado(sesion, almacen, producto)
    ajuste_id = uuid.uuid4()
    folio, cabeza = await folio_del_ajuste(
        sesion, prefijo="AC", ajuste_id=ajuste_id, operacion=operacion
    )
    await sesion.execute(
        text(
            """
            INSERT INTO ajustes_camion
              (id, folio, almacen_id, producto_id, tipo, existencia_al_capturar,
               contado, delta, nota, usuario_id, creado_en, pareja_id)
            VALUES (:id, :folio, :a, :p, 'anulacion', :actual, NULL, :delta,
                    :nota, :quien, :ahora, :cabeza)
            """
        ),
        {
            "id": ajuste_id,
            "cabeza": cabeza,
            "folio": folio,
            "a": almacen,
            "p": producto,
            "actual": actual,
            "delta": delta,
            "nota": nota[:600],
            "quien": quien,
            "ahora": ahora,
        },
    )
    return ajuste_id, folio


# ---------------------------------------------------------------------------
# Anular
# ---------------------------------------------------------------------------

_TABLA_CON_CANDADO = {
    "venta": "ventas",
    "mayoreo": "ventas_mayoreo",
    "no_venta": "no_drops",
    "merma": "mermas",
    "devolucion": "mermas",
    "cambio": "mermas",
    "carga": "cargas",
    "devolucion_bodega": "traspasos",
    "traspaso": "traspasos",
    "entrada": "entradas",
    "salida": "salidas",
    "ajuste": "ajustes_camion",
    "corte": "liquidaciones",
}


async def anular(
    sesion,
    tipo: str,
    ref: uuid.UUID,
    *,
    motivo: str,
    quien: uuid.UUID,
    entiendo_el_corte: bool = False,
    forzar: bool = False,
) -> str:
    """Anula la operación deshaciendo lo que movió. Devuelve el aviso para la
    pantalla. No hace commit: quien llama decide la transacción.

    `forzar` es la casilla de «aunque quede en negativo» (§126)."""
    limpio = " ".join((motivo or "").split())
    if len(limpio) < 10:
        raise AnulacionRechazada(
            "Escribe por qué se anula, con al menos 10 letras: es lo que alguien va "
            "a leer después, y lo que el vendedor ve en su teléfono."
        )
    if tipo not in _TABLA_CON_CANDADO:
        raise OperacionNoExiste("Esa operación no existe.")

    # El documento primero, con candado: dos personas anulando lo mismo a la vez
    # se forman, y la segunda ya lo encuentra anulado.
    await sesion.execute(
        text(f"SELECT 1 FROM {_TABLA_CON_CANDADO[tipo]} WHERE id = :id FOR UPDATE"),
        {"id": ref},
    )
    op = await una(sesion, tipo, ref)
    if op is None:
        raise OperacionNoExiste("Esa operación no existe.")
    plan = await revisar(sesion, op, candado=True)
    if plan.bloqueos:
        raise AnulacionRechazada("No se puede anular: " + " ".join(plan.bloqueos))
    if plan.negativos and not forzar:
        raise AnulacionRechazada(
            "Quedaría en negativo: " + " ".join(plan.negativos) + " Elimina primero lo "
            "que se hizo con esa mercancía, o marca la casilla para eliminarla de "
            "todos modos y corregirlo después con un conteo."
        )
    if plan.corte_cerrado and not entiendo_el_corte:
        raise AnulacionRechazada(
            f"El corte de ese día ({plan.corte_cerrado}) ya se cerró. Marca la casilla "
            "para confirmar que entiendes que ese corte no cambia."
        )

    ahora = datetime.now(UTC)
    anulacion_id = uuid.uuid4()
    nota = f"Anulación de {op.nombre.lower()} {op.folio or ''}: {limpio}"
    datos = {
        "estado_antes": op.estado,
        "detalle": op.detalle,
        "donde": op.donde,
        "momento": op.momento.isoformat() if op.momento else None,
        "renglones": [
            {"producto": r.producto, "cantidad": str(r.cantidad), "de": r.de, "a": r.a}
            for r in plan.renglones
        ],
        "efectos": plan.efectos,
        "corte_cerrado": plan.corte_cerrado,
        "en_negativo": plan.negativos if forzar else [],
    }
    await _registrar_anulacion(
        sesion, op, anulacion_id=anulacion_id, motivo=limpio, quien=quien, ahora=ahora,
        datos=datos,
    )

    folios = await _ANULADORES[tipo](
        sesion, op, motivo=limpio, nota=nota, quien=quien, ahora=ahora,
        anulacion_id=anulacion_id,
    )
    if folios:
        await sesion.execute(
            text(
                "UPDATE anulaciones SET datos = datos || jsonb_build_object('ajustes', "
                "CAST(:f AS jsonb)) WHERE id = :id"
            ),
            {"f": _json(folios), "id": anulacion_id},
        )
    await auditar(
        sesion,
        entidad=tipo,
        entidad_id=ref,
        accion="anular",
        quien=quien,
        antes=datos,
        despues={"estado": "anulada", "ajustes": folios},
        motivo=limpio,
    )
    await panel_ventas._recalcular(sesion, op.fecha)
    return (
        f"{op.nombre} {op.folio or ''} anulada. "
        + " ".join(plan.efectos[:1])
        + (f" Ajuste al teléfono: {folios[0]}"
           + (f", con {len(folios)} artículos." if len(folios) > 1 else ".")
           if folios else "")
    ).replace("  ", " ")


async def _registrar_anulacion(
    sesion, op: Operacion, *, anulacion_id: uuid.UUID, motivo: str, quien, ahora, datos: dict
) -> None:
    """El renglón de `anulaciones`: lo que dice la lista de lo eliminado."""
    try:
        async with sesion.begin_nested():
            await sesion.execute(
                text(
                    """
                    INSERT INTO anulaciones
                      (id, tipo, documento_id, folio, descripcion, vendedor_id, importe,
                       fecha_operativa, motivo, usuario_id, anulada_en, datos)
                    VALUES (:id, :tipo, :doc, :folio, :descripcion, :vendedor, :importe,
                            :fecha, :motivo, :quien, :ahora, CAST(:datos AS jsonb))
                    """
                ),
                {
                    "id": anulacion_id,
                    "tipo": op.tipo,
                    "doc": op.ref,
                    "folio": op.folio,
                    "descripcion": " · ".join(x for x in (op.donde, op.detalle) if x)
                    or op.nombre,
                    "vendedor": op.persona_id,
                    "importe": op.importe,
                    "fecha": op.fecha,
                    "motivo": motivo[:600],
                    "quien": quien,
                    "ahora": ahora,
                    "datos": _json(datos),
                },
            )
    except IntegrityError as e:
        raise AnulacionRechazada("Esa operación ya se había anulado.") from e


def _json(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)


async def _anular_venta(sesion, op, *, motivo, quien, **_) -> list[str]:
    cabecera = await panel_ventas._cabecera(sesion, op.ref, para_escribir=True)
    await panel_ventas.cancelar_venta(
        sesion, cabecera, motivo=motivo, reingresa=True, quien=quien
    )
    return []


async def _anular_mayoreo(sesion, op, *, motivo, quien, **_) -> list[str]:
    await mayoreo.cancelar(sesion, op.ref, motivo=motivo, quien=quien)
    return []


async def _anular_no_venta(sesion, op, **_) -> list[str]:
    # No tiene estado: se borra, y lo que la describía queda en la anulación.
    # Ya no vuelve aunque el teléfono reintente: `sync_operaciones` recuerda que
    # esa operación ya se aplicó.
    await sesion.execute(text("DELETE FROM no_drops WHERE id = :n"), {"n": op.ref})
    return []


async def _anular_merma(sesion, op, *, nota, quien, ahora, anulacion_id, **_) -> list[str]:
    folios = await _deshacer_movimientos(
        sesion,
        await _movimientos(sesion, "merma", op.ref),
        anulacion_id=anulacion_id,
        nota=nota,
        quien=quien,
        ahora=ahora,
        avisar_al_telefono=True,
    )
    await sesion.execute(
        text("UPDATE mermas SET estado = 'cancelada' WHERE id = :m"), {"m": op.ref}
    )
    return folios


async def _anular_carga(
    sesion, op, *, motivo, nota, quien, ahora, anulacion_id, **_
) -> list[str]:
    folios: list[str] = []
    # Primero su corte (§126), con su propia anulación: así sale en la lista de
    # lo eliminado, y su página dice que se fue con la carga.
    for corte in await _cortes_vivos(sesion, op.ref):
        corte_op = await una(sesion, "corte", corte["id"])
        efectos = Plan()
        await _efectos_del_corte(sesion, corte, efectos, reabrir=False)
        de_la_carga = f"Se eliminó con la carga {op.folio}: {motivo}"
        corte_anulacion = uuid.uuid4()
        await _registrar_anulacion(
            sesion, corte_op, anulacion_id=corte_anulacion, motivo=de_la_carga,
            quien=quien, ahora=ahora,
            datos={"estado_antes": corte["estado"], "efectos": efectos.efectos,
                   "con_la_carga": op.folio},
        )
        folios += await _deshacer_corte(
            sesion, corte, motivo=de_la_carga, quien=quien, ahora=ahora,
            anulacion_id=corte_anulacion, reabrir=False,
        )
    await sesion.execute(
        text(
            "UPDATE cortes_vendedor SET estado = 'anulado', "
            "       nota = concat_ws('; ', nota, CAST(:n AS text)) "
            " WHERE carga_id = :c AND estado IN ('pendiente', 'cerrado')"
        ),
        {"c": op.ref, "n": f"se eliminó la carga {op.folio}: {motivo}"[:300]},
    )
    # La solicitud que la pidió queda rechazada: el vendedor lo ve con el motivo y
    # puede pedir otra para ese día (solo hay una aceptada por día).
    await sesion.execute(
        text(
            "UPDATE solicitudes_carga SET estado = 'rechazada', motivo = :m, "
            "       resuelta_en = :ahora, resuelta_por = :q "
            " WHERE carga_id = :c AND estado = 'aceptada'"
        ),
        {"c": op.ref, "m": f"Se anuló la carga {op.folio}: {motivo}"[:300],
         "ahora": ahora, "q": quien},
    )
    if op.estado != "borrador":
        # El teléfono se entera por el delta de la carga cancelada, que la deshace:
        # aquí no va ajuste, o se restaría dos veces.
        await _deshacer_movimientos(
            sesion,
            await _movimientos(sesion, "carga", op.ref),
            anulacion_id=anulacion_id,
            nota=nota,
            quien=quien,
            ahora=ahora,
            avisar_al_telefono=False,
        )
    await sesion.execute(
        text("UPDATE cargas SET estado = 'cancelada' WHERE id = :c"), {"c": op.ref}
    )
    return folios


async def _anular_traspaso(
    sesion, op, *, motivo, nota, quien, ahora, anulacion_id, **_
) -> list[str]:
    folios = await _deshacer_movimientos(
        sesion,
        await _movimientos(sesion, "traspaso", op.ref),
        anulacion_id=anulacion_id,
        nota=nota,
        quien=quien,
        ahora=ahora,
        avisar_al_telefono=True,
    )
    # El cambio de estado es lo que publica el traspaso al teléfono de quien lo
    # pidió (migración 0036): ahí lo ve cancelado.
    await sesion.execute(
        text(
            "UPDATE traspasos SET estado = 'cancelado', motivo_rechazo = :m, "
            "       resuelto_por = :q, resuelto_en = :ahora WHERE id = :t"
        ),
        {"m": motivo[:300], "q": quien, "ahora": ahora, "t": op.ref},
    )
    return folios


async def _anular_entrada(
    sesion, op, *, motivo, nota, quien, ahora, anulacion_id, **_
) -> list[str]:
    if op.estado == "confirmada":
        await _deshacer_movimientos(
            sesion,
            await _movimientos(sesion, "entrada", op.ref),
            anulacion_id=anulacion_id,
            nota=nota,
            quien=quien,
            ahora=ahora,
            avisar_al_telefono=False,
        )
        await sesion.execute(
            text("DELETE FROM cuentas_por_pagar WHERE entrada_id = :e"), {"e": op.ref}
        )
    await sesion.execute(
        text(
            "UPDATE entradas SET estado = 'cancelada', cancelada_en = :ahora, "
            "       cancelada_por = :q, cancelacion_motivo = :m WHERE id = :e"
        ),
        {"ahora": ahora, "q": quien, "m": motivo[:600], "e": op.ref},
    )
    return []


async def _anular_salida(
    sesion, op, *, motivo, nota, quien, ahora, anulacion_id, **_
) -> list[str]:
    if op.estado == "confirmada":
        await _deshacer_movimientos(
            sesion,
            await _movimientos(sesion, "salida", op.ref),
            anulacion_id=anulacion_id,
            nota=nota,
            quien=quien,
            ahora=ahora,
            avisar_al_telefono=False,
        )
    await sesion.execute(
        text(
            "UPDATE salidas SET estado = 'cancelada', cancelada_en = :ahora, "
            "       cancelada_por = :q, cancelacion_motivo = :m WHERE id = :s"
        ),
        {"ahora": ahora, "q": quien, "m": motivo[:600], "s": op.ref},
    )
    return []


async def _anular_ajuste(sesion, op, *, nota, quien, ahora, anulacion_id, **_) -> list[str]:
    # El ajuste no tiene estado: lo que lo anula es el ajuste contrario, que en
    # un camión viaja al teléfono.
    return await _deshacer_movimientos(
        sesion,
        await _movimientos_del_ajuste(sesion, op.ref),
        anulacion_id=anulacion_id,
        nota=nota,
        quien=quien,
        ahora=ahora,
        avisar_al_telefono=True,
    )


async def _anular_corte(
    sesion, op, *, motivo, quien, ahora, anulacion_id, **_
) -> list[str]:
    return await _deshacer_corte(
        sesion, await _corte(sesion, op.ref), motivo=motivo, quien=quien, ahora=ahora,
        anulacion_id=anulacion_id, reabrir=True,
    )


async def _deshacer_corte(
    sesion,
    corte: dict,
    *,
    motivo: str,
    quien: uuid.UUID,
    ahora: datetime,
    anulacion_id: uuid.UUID,
    reabrir: bool,
) -> list[str]:
    """Deshace el corte: sus ajustes al camión, lo que le cargó al vendedor, y el
    corte mismo, que se queda como «anulada» (migración 0061). Devuelve los folios
    de los ajustes que le avisan al teléfono."""
    folios: list[str] = []
    nota = f"Anulación del corte {corte['folio']}: {motivo}"
    if corte["estado"] == "cerrada":
        movimientos = await _movimientos(sesion, "liquidacion", corte["id"])
        if movimientos:
            # El teléfono ya los había sumado con la carga liquidada: el contrario
            # le llega como ajuste del camión, una sola vez.
            folios = await _deshacer_movimientos(
                sesion, movimientos, anulacion_id=anulacion_id, nota=nota, quien=quien,
                ahora=ahora, avisar_al_telefono=True,
            )
        # `cuenta_vendedor` no se borra ni se edita (migración 0039): lo cargado
        # se compensa con una condonación que apunta a este corte.
        cargado = await _cargado_por_el_corte(sesion, corte["id"])
        if cargado > 0:
            ya_hay = (
                await sesion.execute(
                    text("SELECT 1 FROM cuenta_vendedor WHERE liquidacion_id = :l "
                         "   AND origen = 'condonacion'"),
                    {"l": corte["id"]},
                )
            ).first()
            await sesion.execute(
                text(
                    "INSERT INTO cuenta_vendedor (vendedor_id, tipo, origen, importe, fecha, "
                    "  concepto, liquidacion_id, registrado_por) "
                    "VALUES (:v, 'condonacion', 'condonacion', :importe, :fecha, :concepto, "
                    "        :l, :q)"
                ),
                {
                    "v": corte["vendedor_id"],
                    "importe": cargado,
                    "fecha": corte["fecha_operativa"],
                    "concepto": f"Corte {corte['folio']} anulado: {motivo}"[:300],
                    # Una condonación por corte (índice único): si ya había una a
                    # mano, ésta va sin el corte, con el folio en el concepto.
                    "l": None if ya_hay else corte["id"],
                    "q": quien,
                },
            )
    await sesion.execute(
        text("UPDATE liquidaciones SET estado = 'anulada' WHERE id = :l"), {"l": corte["id"]}
    )

    if not reabrir:
        await sesion.execute(
            text(
                "UPDATE cortes_vendedor SET estado = 'anulado', "
                "       nota = concat_ws('; ', nota, CAST(:n AS text)) "
                " WHERE liquidacion_id = :l"
            ),
            {"l": corte["id"], "n": f"corte anulado: {motivo}"[:300]},
        )
        return folios

    # El corte del vendedor vuelve a «por cerrar»; si mientras tanto mandó otro
    # del mismo día, ése manda y éste queda reemplazado.
    await sesion.execute(
        text(
            """
            UPDATE cortes_vendedor k
               SET estado = CASE WHEN EXISTS (
                                   SELECT 1 FROM cortes_vendedor o
                                    WHERE o.vendedor_id = k.vendedor_id
                                      AND o.fecha_operativa = k.fecha_operativa
                                      AND o.estado = 'pendiente' AND o.id <> k.id)
                                 THEN 'reemplazado' ELSE 'pendiente' END,
                   liquidacion_id = NULL, resuelto_por = NULL, resuelto_en = NULL,
                   efectivo_recibido = NULL,
                   nota = concat_ws('; ', k.nota, CAST(:n AS text))
             WHERE k.liquidacion_id = :l
            """
        ),
        {"l": corte["id"], "n": f"corte anulado: {motivo}"[:300]},
    )

    # La carga vuelve a «por cortar». Si ya estaba liquidada, el teléfono la
    # tiene marcada como cerrada con sus ajustes aplicados, y no los volvería a
    # aplicar al cortarla otra vez: por eso pasa un instante por 'cancelada' (el
    # teléfono resta la carga y olvida que la aplicó) y regresa a 'confirmada'
    # (la vuelve a sumar). El camión del teléfono queda igual, y limpio.
    if corte["carga_estado"] == "liquidada":
        await sesion.execute(
            text("UPDATE cargas SET estado = 'cancelada' WHERE id = :c"), {"c": corte["carga_id"]}
        )
    if corte["carga_estado"] in ("liquidada", "en_ruta"):
        await sesion.execute(
            text("UPDATE cargas SET estado = 'confirmada' WHERE id = :c"),
            {"c": corte["carga_id"]},
        )
        # El teléfono toma como activa la carga que le llegó al último. Si el
        # vendedor ya trae una más nueva, se vuelve a publicar después, para que
        # sus ventas sigan amarradas a ésa.
        await sesion.execute(
            text(
                """
                UPDATE cargas SET estado = estado
                 WHERE id = (SELECT id FROM cargas
                              WHERE vendedor_id = :v AND estado IN ('confirmada', 'en_ruta')
                              ORDER BY fecha_operativa DESC, confirmada_en DESC NULLS LAST
                              LIMIT 1)
                   AND id <> :c
                """
            ),
            {"v": corte["vendedor_id"], "c": corte["carga_id"]},
        )
    return folios


_ANULADORES = {
    "venta": _anular_venta,
    "mayoreo": _anular_mayoreo,
    "no_venta": _anular_no_venta,
    "merma": _anular_merma,
    "devolucion": _anular_merma,
    "cambio": _anular_merma,
    "carga": _anular_carga,
    "devolucion_bodega": _anular_traspaso,
    "traspaso": _anular_traspaso,
    "entrada": _anular_entrada,
    "salida": _anular_salida,
    "ajuste": _anular_ajuste,
    "corte": _anular_corte,
}


# ---------------------------------------------------------------------------
# Eliminar varias a la vez (§126)
# ---------------------------------------------------------------------------


@dataclass
class Eliminada:
    """Lo que pasó con cada una al eliminar varias."""

    op: Operacion
    aviso: str | None = None
    error: str | None = None


def en_orden(ops: list[Operacion]) -> list[Operacion]:
    """En el orden en que se eliminan: lo que cuelga de otra cosa primero (ver
    `ORDEN_AL_ELIMINAR`), y dentro de cada tipo lo más nuevo primero."""
    return sorted(
        ops,
        key=lambda o: (
            ORDEN_AL_ELIMINAR.index(o.tipo),
            -(o.fecha.toordinal()),
            -(o.momento.timestamp() if o.momento else 0),
        ),
    )


async def las_marcadas(sesion, claves: list[str]) -> list[Operacion]:
    """Las operaciones de las casillas («tipo:id»), sin repetir y en orden."""
    vistas: set[tuple[str, uuid.UUID]] = set()
    ops: list[Operacion] = []
    for clave in claves[:LIMITE]:
        tipo, _, crudo = clave.partition(":")
        try:
            ref = uuid.UUID(crudo)
        except ValueError:
            continue
        if tipo not in NOMBRE or (tipo, ref) in vistas:
            continue
        vistas.add((tipo, ref))
        op = await una(sesion, tipo, ref)
        if op is not None:
            ops.append(op)
    return en_orden(ops)


async def eliminar_varias(
    sesion,
    ops: list[Operacion],
    *,
    motivo: str,
    quien: uuid.UUID,
    entiendo_el_corte: bool = False,
    forzar: bool = False,
) -> list[Eliminada]:
    """Las elimina una por una, en orden, cada una en su propio punto de
    guardado: la que no se puede se queda como estaba y dice por qué, y las
    demás siguen. No hace commit."""
    resultado: list[Eliminada] = []
    for op in en_orden(ops):
        try:
            async with sesion.begin_nested():
                aviso = await anular(
                    sesion, op.tipo, op.ref, motivo=motivo, quien=quien,
                    entiendo_el_corte=entiendo_el_corte, forzar=forzar,
                )
            resultado.append(Eliminada(op, aviso=aviso))
        except (AnulacionRechazada, OperacionNoExiste) as e:
            resultado.append(Eliminada(op, error=str(e)))
    return resultado

