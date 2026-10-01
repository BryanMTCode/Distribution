"""Las definiciones del tablero de Gerencia. Escritas UNA vez.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTE MÓDULO EXISTE, SI YA ESTÁ `analitica.py`
────────────────────────────────────────────────────────────────────────────
`analitica.py` define las métricas del laboratorio: preguntas abiertas sobre
meses de historia, contestadas desde el esquema estrella, que se refresca al
**cerrar la liquidación**.

Este módulo define las del tablero: seis cifras cerradas sobre el día en curso,
que se recalculan al **sincronizar**. La diferencia es la cadencia, y es la
razón de que no se puedan compartir las mismas vistas (ver el encabezado de la
migración 0021).

Lo que sí se comparte, y no es negociable, son las DEFINICIONES. Una visita es
un cliente-día aquí, en `fact_visitas` y en la pantalla de efectividad del
panel. Si el tablero del gerente dijera "32 visitas" y el reporte de la oficina
"28" por contar documentos en un lado y clientes en el otro, las dos cifras
quedarían inservibles — incluida la que estaba bien.

────────────────────────────────────────────────────────────────────────────
LAS CIFRAS DEL TABLERO SON UN PISO, NO UN TOTAL
────────────────────────────────────────────────────────────────────────────
Es la propiedad más importante de todo lo que hay aquí, y viene del §0.3: lo
único que el servidor puede sumar es lo que ha sincronizado. Un camión sin señal
desde las 10 de la mañana tiene ventas reales que no están en ninguna de estas
cifras.

Por eso `Frescura` acompaña a cada bloque, y por eso incluye el estado del
mundo y no solo la hora del cálculo: "hace 2 minutos" es verdad y es
insuficiente. "Hace 2 minutos, con 2 equipos sin sincronizar" es la frase que
evita una decisión equivocada.

────────────────────────────────────────────────────────────────────────────
"HOY" DEPENDE DE LA ZONA HORARIA DEL SERVIDOR
────────────────────────────────────────────────────────────────────────────
`date.today()` aquí y `CURRENT_DATE` en el SQL usan la zona del sistema, y el
`fecha_operativa` de los documentos lo pone el teléfono con SU día local. Para
que las dos cosas coincidan, el servidor tiene que correr en la hora de la
operación.

No es una suposición nueva de este módulo —el panel entero y la cobranza ya
dependen de ella— pero aquí se nota más: con el reloj en UTC, a partir de las
18:00 locales de México el renglón "de hoy" se escribiría con la fecha de
mañana, y el tablero de la tarde saldría vacío.

`docker-compose.yml` fija `TZ` en todos los servicios y `make doctor` compara la
hora del sistema con la del contenedor de PostgreSQL. Esto queda escrito aquí
porque es la clase de dependencia que no se ve en el código.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Categorías de no-drop que la empresa puede arreglar. Misma lista que la
# pantalla de efectividad del panel: 'cliente' es el mundo, y 'operacion',
# 'producto' y 'vendedor' son decisiones nuestras.
CATEGORIAS_NUESTRAS = ("operacion", "producto", "vendedor")

# Cuántos días hacia atrás puede recalcular una sola corrida del job. Acota el
# trabajo si alguien reactiva un equipo que estuvo un mes guardado y de golpe
# sube treinta días de operación: se recalculan los 30 más recientes de los
# rancios, y la corrida siguiente sigue con el resto en vez de tardar minutos.
MAXIMO_DIAS_POR_CORRIDA = 30


# ---------------------------------------------------------------------------
# Qué días quedaron rancios
# ---------------------------------------------------------------------------
# DEFINICIÓN: un día operativo está rancio si algún documento suyo llegó al
# servidor (`fecha_servidor`) después de la última vez que se calculó su
# renglón (`calculado_en`), o si nunca se calculó.
#
# `fecha_servidor` decide QUÉ DÍAS recalcular — nunca qué renglones sumar. Esa
# distinción es la que separa esto de un ETL incremental, que es justo lo que no
# puede funcionar en un DSD: una venta del lunes que sincroniza el jueves deja
# el LUNES rancio, y el lunes se recalcula completo.
#
# Las cancelaciones entran por `ventas_cancelaciones`: cancelar una venta NO
# toca `ventas.fecha_servidor`, así que sin esa rama el total del día nunca
# bajaría. Es el único cambio de estado que no deja huella en la tabla original.
SQL_DIAS_SUCIOS = """
WITH documentos AS (
    SELECT fecha_operativa AS fecha, fecha_servidor FROM ventas
    UNION ALL
    SELECT fecha_operativa, fecha_servidor FROM cobros
    UNION ALL
    SELECT fecha_operativa, fecha_servidor FROM no_drops
    UNION ALL
    SELECT fecha_operativa, fecha_servidor FROM mermas
    UNION ALL
    SELECT v.fecha_operativa, c.fecha_servidor
      FROM ventas_cancelaciones c
      JOIN ventas v ON v.id = c.venta_id
)
SELECT d.fecha
  FROM documentos d
  LEFT JOIN tablero_dia t ON t.fecha = d.fecha
 GROUP BY d.fecha
HAVING max(d.fecha_servidor) > COALESCE(min(t.calculado_en), '-infinity'::timestamptz)
 ORDER BY d.fecha DESC
 LIMIT :limite
"""


# ---------------------------------------------------------------------------
# El día, por vendedor
# ---------------------------------------------------------------------------
# Se borra el día y se vuelve a insertar, en la misma transacción. Un
# `ON CONFLICT DO UPDATE` dejaría vivo el renglón de un vendedor cuya única
# venta se canceló: su nombre seguiría en el tablero con la cifra de ayer, que
# es peor que no aparecer.
SQL_BORRAR_DIA = "DELETE FROM tablero_dia WHERE fecha = :fecha"

# La visita es un CLIENTE-DÍA, no un documento (ver el encabezado). El
# `UNION ALL` + `bool_or` es lo que permite que un cliente al que se le vendió
# Y se le registró un no-drop el mismo día —se pasó dos veces— cuente como una
# visita vendida: el desenlace fue la venta.
SQL_DIA_VENDEDOR = """
WITH ventas_dia AS (
    SELECT vendedor_id,
           sum(total)                                        AS venta_total,
           sum(total) FILTER (WHERE tipo = 'contado')        AS venta_contado,
           sum(total) FILTER (WHERE tipo = 'credito')        AS venta_credito,
           count(*)                                          AS documentos_venta
      FROM ventas
     WHERE fecha_operativa = :fecha AND estado = 'confirmada'
     GROUP BY vendedor_id
),
visitas_crudas AS (
    SELECT vendedor_id, cliente_id, true AS vendio
      FROM ventas
     WHERE fecha_operativa = :fecha AND estado = 'confirmada'
    UNION ALL
    SELECT vendedor_id, cliente_id, false
      FROM no_drops
     WHERE fecha_operativa = :fecha
),
visitas_unicas AS (
    SELECT vendedor_id, cliente_id, bool_or(vendio) AS vendio
      FROM visitas_crudas
     GROUP BY vendedor_id, cliente_id
),
visitas_dia AS (
    SELECT vendedor_id,
           count(*)                            AS visitas,
           count(*) FILTER (WHERE vendio)      AS visitas_con_venta
      FROM visitas_unicas
     GROUP BY vendedor_id
),
nodrops_dia AS (
    SELECT n.vendedor_id,
           count(*)                                               AS no_drops,
           count(*) FILTER (WHERE m.categoria = ANY(:nuestras))   AS no_drops_nuestros
      FROM no_drops n
      JOIN motivos_no_drop m ON m.codigo = n.motivo_codigo
     WHERE n.fecha_operativa = :fecha
     GROUP BY n.vendedor_id
),
cobros_dia AS (
    SELECT vendedor_id,
           sum(importe)                                              AS cobrado_total,
           sum(importe) FILTER (WHERE forma_pago = 'efectivo')       AS cobrado_efectivo
      FROM cobros
     WHERE fecha_operativa = :fecha AND estado = 'confirmado'
     GROUP BY vendedor_id
),
-- Solo 'merma': la devolución de cliente comparte tabla pero es otra cosa —la
-- mercancía vuelve buena al camión y se puede revender—. Sumarlas juntas haría
-- que un día de muchas devoluciones se leyera como un día de muchas pérdidas.
mermas_dia AS (
    SELECT m.vendedor_id,
           count(DISTINCT m.id)                      AS mermas_documentos,
           COALESCE(sum(d.cantidad_base), 0)         AS mermas_unidades
      FROM mermas m
      LEFT JOIN merma_detalle d ON d.merma_id = m.id
     WHERE m.fecha_operativa = :fecha
       AND m.estado = 'confirmada'
       AND m.tipo = 'merma'
     GROUP BY m.vendedor_id
),
-- El UNION (no UNION ALL) de los cinco: un vendedor que solo cobró, o que solo
-- registró no-drops, tiene que aparecer. Con un JOIN desde ventas, el día en
-- que no vendió nada desaparecería del tablero — justo el día que hay que ver.
todos AS (
    SELECT vendedor_id FROM ventas_dia
    UNION SELECT vendedor_id FROM visitas_dia
    UNION SELECT vendedor_id FROM nodrops_dia
    UNION SELECT vendedor_id FROM cobros_dia
    UNION SELECT vendedor_id FROM mermas_dia
)
INSERT INTO tablero_dia (
    fecha, vendedor_id,
    venta_total, venta_contado, venta_credito, documentos_venta,
    visitas, visitas_con_venta,
    no_drops, no_drops_nuestros,
    cobrado_total, cobrado_efectivo,
    mermas_documentos, mermas_unidades,
    calculado_en
)
SELECT :fecha, t.vendedor_id,
       COALESCE(v.venta_total, 0),
       COALESCE(v.venta_contado, 0),
       COALESCE(v.venta_credito, 0),
       COALESCE(v.documentos_venta, 0),
       COALESCE(s.visitas, 0),
       COALESCE(s.visitas_con_venta, 0),
       COALESCE(n.no_drops, 0),
       COALESCE(n.no_drops_nuestros, 0),
       COALESCE(c.cobrado_total, 0),
       COALESCE(c.cobrado_efectivo, 0),
       COALESCE(mm.mermas_documentos, 0),
       COALESCE(mm.mermas_unidades, 0),
       now()
  FROM todos t
  LEFT JOIN ventas_dia  v  ON v.vendedor_id  = t.vendedor_id
  LEFT JOIN visitas_dia s  ON s.vendedor_id  = t.vendedor_id
  LEFT JOIN nodrops_dia n  ON n.vendedor_id  = t.vendedor_id
  LEFT JOIN cobros_dia  c  ON c.vendedor_id  = t.vendedor_id
  LEFT JOIN mermas_dia  mm ON mm.vendedor_id = t.vendedor_id
"""

# Un renglón vacío para HOY, aunque nadie haya vendido todavía.
#
# Sin esto, el tablero de las 7 de la mañana no podría distinguir "nadie ha
# vendido" de "el tablero no se ha calculado". Son dos cosas muy distintas: la
# primera es información y la segunda es una falla del worker.
SQL_SELLAR_DIA = """
INSERT INTO tablero_dia (fecha, vendedor_id, calculado_en)
SELECT :fecha, u.id, now()
  FROM usuarios u
 WHERE u.rol_codigo = 'vendedor' AND u.activo
ON CONFLICT (fecha, vendedor_id) DO NOTHING
"""


# ---------------------------------------------------------------------------
# El mes, por ruta
# ---------------------------------------------------------------------------
# Grano PARCIAL a propósito: `ventas.ruta_id` es nullable, y aquí se deja fuera
# lo que no la trae. El tablero cuenta esos documentos aparte y lo dice; un
# cajón de "sin ruta" escondido haría que el avance de todas las rutas sumara
# menos que la venta del mes sin explicación.
SQL_BORRAR_MES_RUTA = "DELETE FROM tablero_mes_ruta WHERE periodo = :periodo"

SQL_MES_RUTA = """
WITH rango AS (
    -- `CAST(... AS date)` y no `:periodo::date`: el `::` de PostgreSQL choca
    -- con la sintaxis de parámetros de SQLAlchemy y la consulta llega al
    -- servidor sin sustituir. Mismo motivo que el CAST de `ingesta.py`.
    SELECT CAST(:periodo AS date) AS inicio,
           (CAST(:periodo AS date) + interval '1 month')::date AS fin
),
ventas_mes AS (
    SELECT v.ruta_id,
           sum(v.total)                        AS venta_mes,
           count(DISTINCT v.fecha_operativa)   AS dias_con_venta,
           count(DISTINCT v.cliente_id)        AS clientes_distintos
      FROM ventas v, rango r
     WHERE v.fecha_operativa >= r.inicio AND v.fecha_operativa < r.fin
       AND v.estado = 'confirmada' AND v.ruta_id IS NOT NULL
     GROUP BY v.ruta_id
),
visitas_crudas AS (
    SELECT v.ruta_id, v.cliente_id, v.fecha_operativa, true AS vendio
      FROM ventas v, rango r
     WHERE v.fecha_operativa >= r.inicio AND v.fecha_operativa < r.fin
       AND v.estado = 'confirmada' AND v.ruta_id IS NOT NULL
    UNION ALL
    SELECT n.ruta_id, n.cliente_id, n.fecha_operativa, false
      FROM no_drops n, rango r
     WHERE n.fecha_operativa >= r.inicio AND n.fecha_operativa < r.fin
       AND n.ruta_id IS NOT NULL
),
visitas_unicas AS (
    SELECT ruta_id, cliente_id, fecha_operativa, bool_or(vendio) AS vendio
      FROM visitas_crudas
     GROUP BY ruta_id, cliente_id, fecha_operativa
),
visitas_mes AS (
    SELECT ruta_id,
           count(*)                         AS visitas_mes,
           count(*) FILTER (WHERE vendio)   AS visitas_con_venta_mes
      FROM visitas_unicas
     GROUP BY ruta_id
),
todas AS (
    SELECT ruta_id FROM ventas_mes
    UNION SELECT ruta_id FROM visitas_mes
)
INSERT INTO tablero_mes_ruta (
    periodo, ruta_id, venta_mes, visitas_mes, visitas_con_venta_mes,
    dias_con_venta, clientes_distintos, calculado_en
)
SELECT :periodo, t.ruta_id,
       COALESCE(v.venta_mes, 0),
       COALESCE(s.visitas_mes, 0),
       COALESCE(s.visitas_con_venta_mes, 0),
       COALESCE(v.dias_con_venta, 0),
       COALESCE(v.clientes_distintos, 0),
       now()
  FROM todas t
  LEFT JOIN ventas_mes  v ON v.ruta_id = t.ruta_id
  LEFT JOIN visitas_mes s ON s.ruta_id = t.ruta_id
"""


# ---------------------------------------------------------------------------
# La cartera
# ---------------------------------------------------------------------------
# Un SALDO, no un flujo: no pertenece a ninguna fecha operativa (ver el
# comentario de `tablero_cartera` en la migración). Los tramos son los mismos
# que la pantalla de antigüedad del panel, para que las dos cifras coincidan.
SQL_CARTERA = """
WITH saldos AS (
    SELECT x.cliente_id,
           x.saldo,
           x.fecha_vencimiento,
           CURRENT_DATE - x.fecha_vencimiento AS dias_vencido
      FROM cuentas_por_cobrar x
     WHERE x.estado IN ('abierta', 'parcial')
)
INSERT INTO tablero_cartera (
    id, saldo_total, saldo_vencido,
    vencido_1_15, vencido_16_30, vencido_31_60, vencido_61_mas,
    facturas_abiertas, facturas_vencidas,
    clientes_con_saldo, clientes_vencidos, calculado_en
)
SELECT true,
       COALESCE(sum(saldo), 0),
       COALESCE(sum(saldo) FILTER (WHERE dias_vencido > 0), 0),
       COALESCE(sum(saldo) FILTER (WHERE dias_vencido BETWEEN 1 AND 15), 0),
       COALESCE(sum(saldo) FILTER (WHERE dias_vencido BETWEEN 16 AND 30), 0),
       COALESCE(sum(saldo) FILTER (WHERE dias_vencido BETWEEN 31 AND 60), 0),
       COALESCE(sum(saldo) FILTER (WHERE dias_vencido > 60), 0),
       count(*),
       count(*) FILTER (WHERE dias_vencido > 0),
       count(DISTINCT cliente_id),
       count(DISTINCT cliente_id) FILTER (WHERE dias_vencido > 0),
       now()
  FROM saldos
ON CONFLICT (id) DO UPDATE SET
    saldo_total        = excluded.saldo_total,
    saldo_vencido      = excluded.saldo_vencido,
    vencido_1_15       = excluded.vencido_1_15,
    vencido_16_30      = excluded.vencido_16_30,
    vencido_31_60      = excluded.vencido_31_60,
    vencido_61_mas     = excluded.vencido_61_mas,
    facturas_abiertas  = excluded.facturas_abiertas,
    facturas_vencidas  = excluded.facturas_vencidas,
    clientes_con_saldo = excluded.clientes_con_saldo,
    clientes_vencidos  = excluded.clientes_vencidos,
    calculado_en       = now()
"""


# ---------------------------------------------------------------------------
# El estado del mundo, que acompaña a cada cifra
# ---------------------------------------------------------------------------
# Misma definición que `analitica.py`: un equipo está rezagado si no ha hecho
# push hoy. Y `cola_reportada` suma lo que los propios teléfonos dijeron tener
# pendiente (migración 0019) — cada sobre ahí puede ser una venta que falta en
# el total.
SQL_ESTADO_DEL_MUNDO = """
SELECT count(*) FILTER (
           WHERE d.ultima_sync_push_en IS NULL
              OR d.ultima_sync_push_en < CURRENT_DATE
       )                                                   AS equipos_sin_sincronizar,
       COALESCE(sum(d.cola_pendiente) FILTER (
           WHERE d.cola_reportada_en >= CURRENT_DATE
       ), 0)                                               AS cola_reportada,
       (SELECT count(*) FROM sync_cuarentena
         WHERE estado = 'pendiente')                       AS ops_en_cuarentena
  FROM dispositivos d
 WHERE d.estado = 'activo'
"""


# ---------------------------------------------------------------------------
# Lectura: lo que el endpoint sirve
# ---------------------------------------------------------------------------
# El total del día sale de SUMAR `tablero_dia`, no de volver a consultar
# `ventas`. Es la prueba de que el grano por vendedor es completo: si hubiera
# que ir a la tabla transaccional para el total, el modelo de lectura estaría
# mal elegido.
SQL_RESUMEN_DIA = """
SELECT COALESCE(sum(venta_total), 0)::numeric(14,2)     AS venta_total,
       COALESCE(sum(venta_contado), 0)::numeric(14,2)   AS venta_contado,
       COALESCE(sum(venta_credito), 0)::numeric(14,2)   AS venta_credito,
       COALESCE(sum(documentos_venta), 0)               AS documentos_venta,
       COALESCE(sum(visitas), 0)                        AS visitas,
       COALESCE(sum(visitas_con_venta), 0)              AS visitas_con_venta,
       COALESCE(sum(no_drops), 0)                       AS no_drops,
       COALESCE(sum(no_drops_nuestros), 0)              AS no_drops_nuestros,
       COALESCE(sum(cobrado_total), 0)::numeric(14,2)   AS cobrado_total,
       COALESCE(sum(cobrado_efectivo), 0)::numeric(14,2) AS cobrado_efectivo,
       COALESCE(sum(mermas_documentos), 0)              AS mermas_documentos,
       COALESCE(sum(mermas_unidades), 0)::numeric(14,3) AS mermas_unidades,
       min(calculado_en)                                AS calculado_en,
       count(*)                                         AS renglones
  FROM tablero_dia
 WHERE fecha = :fecha
"""

# Por vendedor, ordenado por venta. Los vendedores sin actividad quedan al
# final con sus ceros en vez de desaparecer: un vendedor que no ha vendido nada
# a mediodía es la información más urgente del tablero.
SQL_DIA_POR_VENDEDOR = """
SELECT t.vendedor_id, u.codigo, u.nombre,
       t.venta_total, t.documentos_venta,
       t.visitas, t.visitas_con_venta,
       t.no_drops, t.no_drops_nuestros,
       t.cobrado_total, t.cobrado_efectivo,
       t.mermas_documentos, t.mermas_unidades,
       t.calculado_en
  FROM tablero_dia t
  JOIN usuarios u ON u.id = t.vendedor_id
 WHERE t.fecha = :fecha
 ORDER BY t.venta_total DESC, u.nombre
"""

# El avance del mes contra el objetivo.
#
# `LEFT JOIN objetivos_ruta` y no `JOIN`: una ruta sin objetivo fijado tiene que
# aparecer con su venta y sin barra de avance. Con un `JOIN`, la ruta a la que
# nadie le puso meta desaparecería del tablero, que es exactamente al revés de
# lo que conviene — la que no tiene meta es la que hay que notar.
SQL_AVANCE_POR_RUTA = """
SELECT r.id AS ruta_id, r.codigo, r.nombre,
       COALESCE(m.venta_mes, 0)::numeric(14,2)    AS venta_mes,
       COALESCE(m.visitas_mes, 0)                 AS visitas_mes,
       COALESCE(m.visitas_con_venta_mes, 0)       AS visitas_con_venta_mes,
       COALESCE(m.dias_con_venta, 0)              AS dias_con_venta,
       COALESCE(m.clientes_distintos, 0)          AS clientes_distintos,
       o.objetivo_venta,
       o.objetivo_visitas,
       m.calculado_en
  FROM rutas r
  LEFT JOIN tablero_mes_ruta m ON m.ruta_id = r.id AND m.periodo = :periodo
  LEFT JOIN objetivos_ruta  o ON o.ruta_id = r.id AND o.periodo = :periodo
 WHERE r.activo
 ORDER BY r.codigo
"""

# Venta del mes que no se pudo atribuir a ninguna ruta. Se muestra, no se
# esconde: es la diferencia entre el total y la suma de las barras.
SQL_VENTA_MES_SIN_RUTA = """
SELECT COALESCE(sum(v.total), 0)::numeric(14,2) AS venta,
       count(*)                                 AS documentos
  FROM ventas v
 WHERE v.estado = 'confirmada'
   AND v.ruta_id IS NULL
   AND v.fecha_operativa >= :periodo
   AND v.fecha_operativa < (CAST(:periodo AS date) + interval '1 month')
"""

# Los puntos del día para el mapa.
#
# Esta consulta SÍ va a las tablas transaccionales, y es la única del tablero
# que lo hace. No contradice la regla del encabezado: no agrega nada, devuelve
# los documentos de UN día acotados por un índice (`idx_ventas_operativa`,
# `idx_nodrops_operativa`) y con LIMIT. Precalcular una copia de las mismas
# coordenadas sería duplicar renglón por renglón lo que ya está indexado.
SQL_MAPA_DEL_DIA = """
SELECT 'venta' AS clase, v.lat, v.lng, v.total AS importe,
       c.nombre_comercial AS cliente, u.codigo AS vendedor, NULL AS motivo,
       v.fecha_dispositivo AS momento
  FROM ventas v
  JOIN clientes c ON c.id = v.cliente_id
  JOIN usuarios u ON u.id = v.vendedor_id
 WHERE v.fecha_operativa = :fecha AND v.estado = 'confirmada'
   AND v.lat IS NOT NULL AND v.lng IS NOT NULL
UNION ALL
SELECT 'no_drop', n.lat, n.lng, NULL,
       c.nombre_comercial, u.codigo, m.nombre,
       n.fecha_dispositivo
  FROM no_drops n
  JOIN clientes c ON c.id = n.cliente_id
  JOIN usuarios u ON u.id = n.vendedor_id
  JOIN motivos_no_drop m ON m.codigo = n.motivo_codigo
 WHERE n.fecha_operativa = :fecha
 ORDER BY momento
 LIMIT :limite
"""

# Tope de puntos del mapa. Un día de ocho rutas son cientos de visitas; miles
# de puntos en un lienzo de teléfono no se distinguen entre sí y solo costarían
# transferencia en la calle.
MAXIMO_PUNTOS_MAPA = 1000


# ---------------------------------------------------------------------------
# Frescura
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Frescura:
    """De cuándo son los números y qué tan completo estaba el mundo entonces.

    Es la misma idea que `analitica.Frescura` con un umbral distinto, y la
    diferencia está justificada: el laboratorio contesta preguntas del mes y
    tolera dos horas de retraso; el tablero contesta "cómo va el día" y a los
    quince minutos ya puede estar guiando una llamada telefónica a un vendedor.

    Se mantiene como clase propia a propósito. Compartirla obligaría a que las
    dos pantallas tuvieran el mismo umbral, y el día que alguien ajustara el del
    laboratorio movería en silencio el del tablero.
    """

    minutos: int
    equipos_sin_sincronizar: int
    cola_reportada: int
    ops_en_cuarentena: int

    @property
    def confiable(self) -> bool:
        return (
            self.minutos <= 15
            and self.equipos_sin_sincronizar == 0
            and self.cola_reportada == 0
            and self.ops_en_cuarentena == 0
        )

    @property
    def advertencia(self) -> str | None:
        """La frase que acompaña a la cifra. `None` cuando no hace falta.

        Se enumeran todos los motivos y no solo el primero: un tablero que dice
        "2 equipos sin sincronizar" y se calla las 5 operaciones en cuarentena
        deja a quien lo lee creyendo que ya sabe todo lo que falta.
        """
        partes: list[str] = []
        if self.minutos > 15:
            partes.append(
                f"el tablero se calculó hace {self.minutos} min"
                if self.minutos < 120
                else f"el tablero se calculó hace {self.minutos // 60} h"
            )
        if self.equipos_sin_sincronizar:
            partes.append(
                f"{self.equipos_sin_sincronizar} equipo(s) sin sincronizar hoy, "
                "así que estas cifras son un piso y no un total"
            )
        if self.cola_reportada:
            partes.append(
                f"{self.cola_reportada} operación(es) todavía en los teléfonos"
            )
        if self.ops_en_cuarentena:
            partes.append(
                f"{self.ops_en_cuarentena} operación(es) en cuarentena, "
                "que no están contadas en ninguna cifra"
            )
        if not partes:
            return None
        return "; ".join(partes).capitalize() + "."


# ---------------------------------------------------------------------------
# Aritmética del avance
# ---------------------------------------------------------------------------
def inicio_de_mes(dia: date) -> date:
    return dia.replace(day=1)


def porcentaje(parte: Decimal | int, total: Decimal | int) -> Decimal:
    """Un porcentaje con un decimal. Total cero da cero, no una excepción."""
    if not total:
        return Decimal("0.0")
    return (Decimal(parte) * 100 / Decimal(total)).quantize(Decimal("0.1"))


@dataclass(frozen=True)
class Avance:
    """El avance de una ruta contra su objetivo del mes.

    ────────────────────────────────────────────────────────────────────────
    POR QUÉ NO BASTA EL PORCENTAJE
    ────────────────────────────────────────────────────────────────────────
    "67% del objetivo" el día 10 es excelente y el día 28 es un problema, y el
    número es el mismo. Sin el avance ESPERADO al lado, la cifra se lee mal
    exactamente a mitad de mes, que es cuando todavía se puede corregir.

    El esperado se prorratea por días naturales transcurridos y no por días
    hábiles. Es deliberado y hay que decirlo: una ruta que trabaja de lunes a
    sábado parecerá atrasada los domingos. La alternativa —un calendario de días
    hábiles por ruta— es un dato que nadie va a mantener, y un prorrateo que
    depende de una tabla desactualizada miente todo el mes en vez de un día.
    """

    venta: Decimal
    objetivo: Decimal | None
    dia_del_mes: int
    dias_del_mes: int

    @property
    def logrado(self) -> Decimal | None:
        if self.objetivo is None or self.objetivo == 0:
            return None
        return porcentaje(self.venta, self.objetivo)

    @property
    def esperado(self) -> Decimal:
        return porcentaje(self.dia_del_mes, self.dias_del_mes)

    @property
    def diferencia(self) -> Decimal | None:
        """Puntos por encima (positivo) o por debajo (negativo) de lo esperado."""
        if self.logrado is None:
            return None
        return self.logrado - self.esperado

    @property
    def semaforo(self) -> str:
        """'sin_objetivo' | 'adelante' | 'cerca' | 'atras'.

        El tramo de 'cerca' es de diez puntos y existe porque un tablero que
        pinta de rojo una ruta que va dos puntos abajo enseña a ignorar el rojo.
        """
        if (diferencia := self.diferencia) is None:
            return "sin_objetivo"
        if diferencia >= 0:
            return "adelante"
        if diferencia >= -10:
            return "cerca"
        return "atras"
