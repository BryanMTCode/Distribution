"""Las definiciones de las métricas del laboratorio. Escritas UNA vez.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTO ES UN MÓDULO Y NO SQL SUELTO EN STREAMLIT
────────────────────────────────────────────────────────────────────────────
El riesgo del laboratorio no es que una consulta sea lenta: es que **la misma
pregunta se contesta distinto cada vez que alguien la hace**. "Drop size" puede
significar tres cosas, y dos reportes del mismo mes con dos números destruyen la
confianza en los dos.

Cada métrica de aquí abajo lleva su definición escrita al lado, y el SQL es la
única implementación. Si la definición cambia, cambia en un lugar y los dos
reportes cambian juntos.

────────────────────────────────────────────────────────────────────────────
LAS TRES COSAS QUE SE MIDEN MAL EN UN DSD
────────────────────────────────────────────────────────────────────────────
1. **Contar documentos en vez de visitas.** Dos remisiones al mismo cliente el
   mismo día son UNA visita. Contarlas como dos infla la efectividad, desinfla el
   drop size, y premia al vendedor que parte un pedido en dos.

2. **Promediar sobre los días que hubo venta.** Un vendedor que vendió $10,000 en
   tres de cinco días no promedió $3,333: promedió $2,000. Por eso los promedios
   por día se calculan contra `dim_tiempo`, que tiene los días vacíos.

3. **Un umbral fijo de abandono.** "Sin comprar en 30 días" marca como perdido a
   un cliente que siempre compra cada 45, e ignora a uno que compraba cada
   semana y lleva 20 días. El riesgo se mide contra **la cadencia propia de cada
   cliente**, no contra una constante.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# ---------------------------------------------------------------------------
# Drop size
# ---------------------------------------------------------------------------
# DEFINICIÓN: importe promedio de una visita QUE VENDIÓ.
#
# Es la métrica central de un DSD porque responde a la palanca más barata: subir
# el drop size no cuesta una visita más, cuesta vender mejor en la que ya se hizo.
#
# El denominador son las visitas con venta, NO todas las visitas. La segunda
# también es útil y es otra cosa —"importe por visita", que mezcla el drop size
# con la efectividad— así que se devuelven las dos, con nombres distintos, para
# que nadie tenga que adivinar cuál está viendo.
SQL_DROP_SIZE = """
SELECT t.{agrupacion}                                        AS periodo,
       count(*) FILTER (WHERE v.vendio)                      AS visitas_con_venta,
       count(*)                                              AS visitas,
       COALESCE(sum(v.importe), 0)::numeric(14,2)            AS importe,
       -- Drop size: lo que deja una visita en la que SÍ se vendió.
       CASE WHEN count(*) FILTER (WHERE v.vendio) > 0
            THEN (sum(v.importe) / count(*) FILTER (WHERE v.vendio))::numeric(14,2)
       END                                                   AS drop_size,
       -- Importe por visita: incluye las perdidas. Es drop_size x efectividad.
       CASE WHEN count(*) > 0
            THEN (COALESCE(sum(v.importe), 0) / count(*))::numeric(14,2)
       END                                                   AS importe_por_visita,
       CASE WHEN count(*) > 0
            THEN (100.0 * count(*) FILTER (WHERE v.vendio) / count(*))::numeric(5,1)
       END                                                   AS efectividad_pct
  FROM fact_visitas v
  JOIN dim_tiempo t ON t.fecha = v.fecha
 WHERE v.fecha BETWEEN %(desde)s AND %(hasta)s
 GROUP BY t.{agrupacion}
 ORDER BY t.{agrupacion}
"""

# Agrupaciones permitidas. La agrupación entra en el SQL por interpolación —no se
# puede parametrizar un GROUP BY— así que va contra lista blanca: sin esto, un
# selector de la interfaz sería inyección de SQL.
AGRUPACIONES: dict[str, str] = {
    "dia": "fecha",
    "semana": "inicio_semana",
    "mes": "inicio_mes",
}


def sql_drop_size(agrupacion: str) -> str:
    if agrupacion not in AGRUPACIONES:
        raise ValueError(f"agrupación inválida: {agrupacion!r}")
    return SQL_DROP_SIZE.format(agrupacion=AGRUPACIONES[agrupacion])


# ---------------------------------------------------------------------------
# Productividad por vendedor
# ---------------------------------------------------------------------------
# DEFINICIÓN: visitas, efectividad, drop size e importe por DÍA TRABAJADO.
#
# "Por día trabajado" y no "por día del periodo": un vendedor que estuvo de
# vacaciones una semana no tiene por qué salir mal en el promedio diario. Un día
# trabajado es un día en que registró al menos una visita — que es el único dato
# que tenemos, y es honesto decirlo así.
SQL_PRODUCTIVIDAD = """
SELECT d.vendedor_id,
       d.codigo,
       d.nombre,
       d.rutas,
       count(DISTINCT v.fecha)                               AS dias_trabajados,
       count(*)                                              AS visitas,
       count(*) FILTER (WHERE v.vendio)                       AS ventas,
       COALESCE(sum(v.importe), 0)::numeric(14,2)             AS importe,
       CASE WHEN count(*) > 0
            THEN (100.0 * count(*) FILTER (WHERE v.vendio) / count(*))::numeric(5,1)
       END                                                    AS efectividad_pct,
       CASE WHEN count(*) FILTER (WHERE v.vendio) > 0
            THEN (sum(v.importe) / count(*) FILTER (WHERE v.vendio))::numeric(14,2)
       END                                                    AS drop_size,
       CASE WHEN count(DISTINCT v.fecha) > 0
            THEN (count(*)::numeric / count(DISTINCT v.fecha))::numeric(6,1)
       END                                                    AS visitas_por_dia,
       CASE WHEN count(DISTINCT v.fecha) > 0
            THEN (COALESCE(sum(v.importe), 0) / count(DISTINCT v.fecha))::numeric(14,2)
       END                                                    AS importe_por_dia
  FROM fact_visitas v
  JOIN dim_vendedor d ON d.vendedor_id = v.vendedor_id
 WHERE v.fecha BETWEEN %(desde)s AND %(hasta)s
 GROUP BY d.vendedor_id, d.codigo, d.nombre, d.rutas
 ORDER BY importe DESC
"""


# ---------------------------------------------------------------------------
# Frecuencia de visita
# ---------------------------------------------------------------------------
# DEFINICIÓN: días que pasan, en MEDIANA, entre dos visitas al mismo cliente.
#
# Mediana y no promedio: un cliente visitado semanalmente al que un día se le
# dejó de ir dos meses tiene un promedio de 14 días que no describe ninguna
# semana real. La mediana aguanta ese hueco.
#
# Exige al menos tres visitas: con dos, la "frecuencia" es un solo intervalo y no
# hay nada que promediar.
SQL_FRECUENCIA_VISITA = """
WITH intervalos AS (
    SELECT v.cliente_id,
           v.fecha - lag(v.fecha) OVER (PARTITION BY v.cliente_id ORDER BY v.fecha)
               AS dias
      FROM fact_visitas v
     WHERE v.fecha BETWEEN %(desde)s AND %(hasta)s
)
SELECT c.cliente_id,
       c.codigo,
       c.nombre_comercial,
       c.canal,
       c.ruta_codigo,
       count(i.dias)                                          AS intervalos,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY i.dias)::numeric(6,1)
                                                              AS mediana_dias,
       min(i.dias)                                            AS minimo_dias,
       max(i.dias)                                            AS maximo_dias
  FROM intervalos i
  JOIN dim_cliente c ON c.cliente_id = i.cliente_id
 WHERE i.dias IS NOT NULL
 GROUP BY c.cliente_id, c.codigo, c.nombre_comercial, c.canal, c.ruta_codigo
HAVING count(i.dias) >= 2
 ORDER BY mediana_dias DESC NULLS LAST
"""


# ---------------------------------------------------------------------------
# Clientes en riesgo de abandono
# ---------------------------------------------------------------------------
# DEFINICIÓN: clientes cuyos días sin comprar superan su PROPIA cadencia
# histórica por un factor.
#
# Un umbral fijo —"sin comprar en 30 días"— es la forma fácil y es engañosa en
# las dos direcciones: marca como perdido al cliente de barrio que siempre compra
# cada 45 días, y deja pasar al que compraba cada semana y lleva 20 sin aparecer.
# El segundo es el que de verdad se está yendo, y es el que un umbral fijo
# esconde.
#
# Hacen falta al menos 3 compras para tener una cadencia que signifique algo. Con
# menos, el cliente es nuevo y no hay de qué desviarse: aparece en su propia
# categoría en vez de mezclarse con los que se están yendo.
SQL_CLIENTES_EN_RIESGO = """
WITH compras AS (
    SELECT v.cliente_id, v.fecha, v.importe
      FROM fact_visitas v
     WHERE v.vendio
),
cadencia AS (
    SELECT c.cliente_id,
           count(*)                                           AS compras,
           max(c.fecha)                                       AS ultima_compra,
           sum(c.importe)::numeric(14,2)                       AS importe_historico
      FROM compras c
     GROUP BY c.cliente_id
),
-- La mediana de intervalos va en CTEs aparte, y no por estilo: PostgreSQL no
-- admite una función de ventana dentro de un agregado, así que `percentile_cont`
-- sobre un `lag` tiene que calcularse en dos pasos.
intervalos AS (
    SELECT cliente_id,
           fecha - lag(fecha) OVER (PARTITION BY cliente_id ORDER BY fecha) AS dias
      FROM compras
),
medianas AS (
    SELECT cliente_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY dias)::numeric(6,1) AS cadencia_dias
      FROM intervalos
     WHERE dias IS NOT NULL
     GROUP BY cliente_id
)
SELECT c.cliente_id,
       c.codigo,
       c.nombre_comercial,
       c.canal,
       c.ruta_codigo,
       cad.compras,
       cad.ultima_compra,
       cad.importe_historico,
       (CURRENT_DATE - cad.ultima_compra)                      AS dias_sin_comprar,
       m.cadencia_dias,
       CASE WHEN m.cadencia_dias > 0
            THEN ((CURRENT_DATE - cad.ultima_compra) / m.cadencia_dias)::numeric(6,2)
       END                                                     AS veces_su_cadencia,
       CASE
           WHEN cad.compras < 3                     THEN 'nuevo'
           WHEN m.cadencia_dias IS NULL             THEN 'nuevo'
           WHEN (CURRENT_DATE - cad.ultima_compra) > m.cadencia_dias * %(factor_perdido)s
                                                    THEN 'perdido'
           WHEN (CURRENT_DATE - cad.ultima_compra) > m.cadencia_dias * %(factor_riesgo)s
                                                    THEN 'en_riesgo'
           ELSE 'al_corriente'
       END                                                     AS situacion
  FROM cadencia cad
  JOIN dim_cliente c ON c.cliente_id = cad.cliente_id
  LEFT JOIN medianas m ON m.cliente_id = cad.cliente_id
 WHERE c.estatus = 'activo'
 ORDER BY veces_su_cadencia DESC NULLS LAST
"""

# Un cliente que lleva el doble de su cadencia sin aparecer está en riesgo; al
# triple, ya se fue. No son números con fundamento estadístico: son los que hacen
# que la lista de "en riesgo" sea corta y por tanto accionable. Si un día la
# lista se vuelve ruido, estos son los dos números que hay que mover.
FACTOR_RIESGO = Decimal("2.0")
FACTOR_PERDIDO = Decimal("3.0")


# ---------------------------------------------------------------------------
# Rotación de producto
# ---------------------------------------------------------------------------
# DEFINICIÓN: unidades base vendidas en el periodo, contra la existencia promedio
# del mismo periodo, reconstruida del libro mayor.
#
# **El punto fino:** no hay fotos diarias del inventario. `existencias` es una
# caché del valor ACTUAL, así que no sirve para el pasado. Lo que sí hay es
# `movimientos_inventario`, que es append-only por disparador: la existencia de
# cualquier fecha se reconstruye sumando los movimientos hasta esa fecha.
#
# Eso significa que la rotación histórica es calculable sin haber planeado
# capturarla — y vale la pena decirlo porque la decisión de que el libro fuera
# append-only se tomó por auditoría, no por analítica, y resultó que paga dos
# veces.
#
# `dias_con_existencia` es la cifra que acompaña: una rotación altísima sobre un
# producto que solo estuvo tres días en el camión no es un éxito de ventas, es un
# desabasto. Sin ese número, la rotación premia justo lo que hay que corregir.
SQL_ROTACION = """
WITH vendido AS (
    SELECT producto_id,
           sum(cantidad_base)::numeric(14,3) AS unidades,
           sum(importe)::numeric(14,2)       AS importe,
           count(DISTINCT venta_id)          AS tickets,
           count(DISTINCT cliente_id)        AS clientes
      FROM fact_ventas
     WHERE fecha BETWEEN %(desde)s AND %(hasta)s
     GROUP BY producto_id
),
-- Existencia al cierre de cada día del periodo, reconstruida sumando TODO el
-- libro mayor hasta esa fecha. El acumulado se hace con un self-join sobre
-- `m.fecha <= t.fecha` y no con una función de ventana, porque la ventana solo
-- vería los movimientos del periodo: la existencia del primer día del mes
-- depende de todo lo que pasó antes, y empezar el acumulado en cero daría
-- existencias negativas para cualquier rango que no arranque en el día uno.
--
-- Solo almacenes de tipo camión: la rotación que importa es la del producto en
-- la calle, no la de la bodega.
saldo_diario AS (
    SELECT t.fecha,
           m.producto_id,
           sum(m.delta)::numeric(14,3) AS existencia
      FROM dim_tiempo t
      JOIN fact_movimientos m ON m.fecha <= t.fecha
      JOIN almacenes a ON a.id = m.almacen_id AND a.tipo = 'camion'
     WHERE t.fecha BETWEEN %(desde)s AND %(hasta)s
     GROUP BY t.fecha, m.producto_id
),
inventario AS (
    SELECT producto_id,
           avg(existencia)::numeric(14,3)                   AS existencia_promedio,
           -- La cifra que acompaña a la rotación y la hace legible: una rotación
           -- altísima sobre un producto que estuvo tres días en el camión no es
           -- éxito de ventas, es desabasto. Sin este número, la métrica premia
           -- justo lo que hay que corregir.
           count(*) FILTER (WHERE existencia > 0)            AS dias_con_existencia,
           count(*)                                          AS dias_del_periodo
      FROM saldo_diario
     GROUP BY producto_id
)
SELECT p.producto_id,
       p.sku,
       p.nombre,
       p.categoria,
       p.marca,
       p.unidad_base,
       COALESCE(v.unidades, 0)   AS unidades_vendidas,
       COALESCE(v.importe, 0)    AS importe,
       COALESCE(v.tickets, 0)    AS tickets,
       COALESCE(v.clientes, 0)   AS clientes,
       -- En presentación grande, que es como lo piensa quien compra.
       CASE WHEN p.factor_maximo > 0
            THEN (COALESCE(v.unidades, 0) / p.factor_maximo)::numeric(14,2)
       END                       AS en_unidad_mayor,
       p.unidad_mayor,
       i.existencia_promedio,
       i.dias_con_existencia,
       i.dias_del_periodo,
       -- Rotación: cuántas veces se vendió el inventario promedio del periodo.
       CASE WHEN i.existencia_promedio > 0
            THEN (COALESCE(v.unidades, 0) / i.existencia_promedio)::numeric(10,2)
       END                       AS rotacion
  FROM dim_producto p
  LEFT JOIN vendido v    ON v.producto_id = p.producto_id
  LEFT JOIN inventario i ON i.producto_id = p.producto_id
 WHERE p.activo
 ORDER BY COALESCE(v.importe, 0) DESC
"""


# ---------------------------------------------------------------------------
# La antigüedad de los datos
# ---------------------------------------------------------------------------
SQL_FRESCURA = """
SELECT vista,
       refrescado_en,
       duracion_ms,
       renglones,
       equipos_sin_sincronizar,
       ops_en_cuarentena,
       EXTRACT(EPOCH FROM (now() - refrescado_en))::integer AS segundos_desde
  FROM analitica_refrescos
 ORDER BY refrescado_en
"""


@dataclass(frozen=True)
class Frescura:
    """De cuándo son los números, y qué tan completo estaba el mundo entonces.

    Las dos cosas se muestran juntas porque juntas son la advertencia. "Hace 5
    minutos" suena perfecto, pero si en ese momento tres equipos no habían
    sincronizado, las cifras del día están incompletas y el número de ventas es
    un piso, no un total.
    """

    minutos: int
    equipos_sin_sincronizar: int
    ops_en_cuarentena: int

    @property
    def confiable(self) -> bool:
        """Si se puede decidir con estos números sin advertencia.

        Dos horas es el umbral: por debajo, el refresh es de esta jornada. Y cero
        equipos rezagados, porque un solo teléfono sin sincronizar puede cambiar
        el total del día de una ruta.
        """
        return (
            self.minutos <= 120
            and self.equipos_sin_sincronizar == 0
            and self.ops_en_cuarentena == 0
        )

    @property
    def advertencia(self) -> str | None:
        partes: list[str] = []
        if self.minutos > 120:
            horas = self.minutos // 60
            partes.append(
                f"los datos se recalcularon hace {horas} h: pudo entrar operación después"
            )
        if self.equipos_sin_sincronizar:
            partes.append(
                f"{self.equipos_sin_sincronizar} equipo(s) sin sincronizar, "
                "así que las cifras del día son un piso y no un total"
            )
        if self.ops_en_cuarentena:
            partes.append(
                f"{self.ops_en_cuarentena} operación(es) en cuarentena, "
                "que no están contadas en ningún número de aquí"
            )
        if not partes:
            return None
        return "; ".join(partes).capitalize() + "."
