-- ===========================================================================
-- 0020 · Fase 8 — El esquema estrella del laboratorio analítico
-- ===========================================================================
-- Las tablas transaccionales están normalizadas para escribir rápido y no
-- mentir: `ventas`, `venta_partidas`, `movimientos_inventario`. Contestar
-- "¿cuál es el drop size por canal este mes?" sobre ellas son cinco JOIN y una
-- definición de visita escrita a mano **cada vez que alguien pregunta**. Y ahí
-- está el problema real: la definición se escribe distinto cada vez, y entonces
-- dos reportes del mismo mes dan dos números.
--
-- Este esquema existe para que la definición se escriba UNA vez.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ VISTAS MATERIALIZADAS Y NO UN ETL INCREMENTAL
-- ---------------------------------------------------------------------------
-- Es la decisión que más importa aquí, y la razón es propia de un DSD.
--
-- Un ETL incremental carga "lo creado desde la última corrida". En un sistema
-- normal eso funciona. Aquí **los datos llegan tarde por diseño**: una venta del
-- lunes puede sincronizar el jueves porque el teléfono no tuvo señal (§0.3). Un
-- ETL incremental por `creado_en` la cargaría el jueves con fecha de jueves —o
-- no la cargaría nunca si filtra por `fecha_operativa`—, y el lunes quedaría
-- subreportado para siempre, sin que nada lo avise.
--
-- Un `REFRESH MATERIALIZED VIEW` completo no tiene ese defecto: recalcula todo
-- desde la verdad transaccional. A volumen de miles de tickets diarios cuesta
-- segundos, y es *imposible* que se desincronice. Cuando el volumen lo pida, el
-- camino es particionar por fecha, no volverse incremental.
--
-- ---------------------------------------------------------------------------
-- DOS TRAMPAS DE `REFRESH ... CONCURRENTLY`, VERIFICADAS
-- ---------------------------------------------------------------------------
-- 1. **Exige un índice UNIQUE sin WHERE.** Sin él:
--       ERROR: cannot refresh materialized view ... concurrently
--    Por eso cada vista de abajo lleva el suyo, y no es decorativo.
-- 2. **No funciona sobre una vista nunca poblada:**
--       ERROR: CONCURRENTLY cannot be used when the materialized view is not populated
--    Por eso aquí se crean CON DATOS. El job además lo comprueba con
--    `pg_class.relispopulated` y cae a un refresh simple si hiciera falta, para
--    que no exista forma de que falle (ver `app/workers/analitica.py`).
--
-- `CONCURRENTLY` importa: sin él, el refresh toma un ACCESS EXCLUSIVE y el
-- laboratorio se congela a media consulta cada vez que corre el job.
--
-- ---------------------------------------------------------------------------
-- EL ROL DE SOLO LECTURA YA LAS CUBRE
-- ---------------------------------------------------------------------------
-- `db/ops/rol_analitico.sql` hace `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON
-- TABLES`, y en PostgreSQL eso **sí** alcanza a las vistas materializadas
-- —comprobado, porque es contraintuitivo: `relkind='m'` no es `'r'`—. No hay que
-- volver a conceder nada al crear esto.
-- ===========================================================================


-- ===========================================================================
-- dim_tiempo — la única dimensión que es tabla de verdad
-- ===========================================================================
-- Es estática: el calendario de 2024 no cambia. Y hace falta como TABLA, no
-- como `generate_series` al vuelo, por una razón concreta: un reporte de ventas
-- por día tiene que mostrar **los días sin venta**. Con un JOIN contra las
-- ventas, el martes que nadie vendió simplemente no existe en el resultado, y
-- una gráfica sin ese hueco miente por omisión — el promedio sale repartido
-- entre menos días de los que hubo.
CREATE TABLE IF NOT EXISTS dim_tiempo (
    fecha               date PRIMARY KEY,
    anio                smallint NOT NULL,
    mes                 smallint NOT NULL,
    dia                 smallint NOT NULL,
    trimestre           smallint NOT NULL,
    -- ISO: la semana empieza en lunes y el "año ISO" puede no ser el del mes.
    -- El 1 de enero puede caer en la semana 52 del año anterior, y agrupar por
    -- `anio, semana` sin eso parte una semana en dos renglones.
    semana_iso          smallint NOT NULL,
    anio_iso            smallint NOT NULL,
    dia_semana          smallint NOT NULL,   -- 1 = lunes … 7 = domingo
    nombre_dia          text NOT NULL,
    nombre_mes          text NOT NULL,
    es_fin_de_semana    boolean NOT NULL,
    inicio_semana       date NOT NULL,
    inicio_mes          date NOT NULL
);

COMMENT ON TABLE dim_tiempo IS
    'Calendario por día. Tabla y no generate_series para que los días SIN venta '
    'aparezcan en los reportes: una serie sin sus huecos miente por omisión.';

-- 2024-01-01 a 2034-12-31. Se rellena una vez; `ON CONFLICT DO NOTHING` deja
-- que la migración sea reaplicable sin duplicar.
INSERT INTO dim_tiempo (
    fecha, anio, mes, dia, trimestre, semana_iso, anio_iso,
    dia_semana, nombre_dia, nombre_mes, es_fin_de_semana, inicio_semana, inicio_mes
)
SELECT d::date,
       EXTRACT(YEAR    FROM d)::smallint,
       EXTRACT(MONTH   FROM d)::smallint,
       EXTRACT(DAY     FROM d)::smallint,
       EXTRACT(QUARTER FROM d)::smallint,
       EXTRACT(WEEK    FROM d)::smallint,
       EXTRACT(ISOYEAR FROM d)::smallint,
       EXTRACT(ISODOW  FROM d)::smallint,
       CASE EXTRACT(ISODOW FROM d)
           WHEN 1 THEN 'lunes'    WHEN 2 THEN 'martes'  WHEN 3 THEN 'miércoles'
           WHEN 4 THEN 'jueves'   WHEN 5 THEN 'viernes' WHEN 6 THEN 'sábado'
           ELSE 'domingo' END,
       CASE EXTRACT(MONTH FROM d)
           WHEN 1 THEN 'enero'      WHEN 2 THEN 'febrero'   WHEN 3 THEN 'marzo'
           WHEN 4 THEN 'abril'      WHEN 5 THEN 'mayo'      WHEN 6 THEN 'junio'
           WHEN 7 THEN 'julio'      WHEN 8 THEN 'agosto'    WHEN 9 THEN 'septiembre'
           WHEN 10 THEN 'octubre'   WHEN 11 THEN 'noviembre' ELSE 'diciembre' END,
       EXTRACT(ISODOW FROM d) >= 6,
       date_trunc('week',  d)::date,
       date_trunc('month', d)::date
  FROM generate_series('2024-01-01'::date, '2034-12-31'::date, '1 day') AS d
ON CONFLICT (fecha) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_dim_tiempo_mes    ON dim_tiempo (inicio_mes);
CREATE INDEX IF NOT EXISTS idx_dim_tiempo_semana ON dim_tiempo (anio_iso, semana_iso);


-- ===========================================================================
-- El registro de refrescos — lo que hace honesto al laboratorio
-- ===========================================================================
-- §0.3: "tiempo real" es "tiempo real de lo que ya sincronizó". Un número sin
-- su antigüedad se trata como la verdad, y estos números son, por definición,
-- una foto de cuando corrió el job. Sin esta tabla, el laboratorio no podría
-- decir de cuándo es lo que muestra — y mostrar una cifra de ayer como si fuera
-- de ahora es la forma más fácil de que alguien decida con datos viejos.
CREATE TABLE IF NOT EXISTS analitica_refrescos (
    vista           text PRIMARY KEY,
    refrescado_en   timestamptz NOT NULL DEFAULT now(),
    duracion_ms     integer,
    renglones       bigint,
    -- El dato que sigue en importancia a la hora: qué tan completo estaba el
    -- mundo cuando se calculó. Si tres equipos no habían sincronizado, las
    -- cifras del día están incompletas y hay que decirlo.
    equipos_sin_sincronizar smallint,
    ops_en_cuarentena       integer
);

COMMENT ON TABLE analitica_refrescos IS
    'Cuándo se recalculó cada vista y qué tan completo estaba el mundo entonces. '
    'Sin esto el laboratorio no puede decir de cuándo son sus números.';


-- ===========================================================================
-- DIMENSIONES
-- ===========================================================================
-- Se materializan en vez de leerse directo de `clientes`/`productos` por una
-- razón que no es rendimiento: **la dimensión resuelve los códigos a nombres y
-- normaliza los nulos**. `canal_codigo IS NULL` aparece como «Sin canal» una vez
-- aquí, en lugar de en cada consulta del laboratorio con un COALESCE distinto.

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_cliente AS
SELECT c.id                                        AS cliente_id,
       COALESCE(c.codigo, '(sin código)')           AS codigo,
       c.nombre_comercial,
       c.estatus,
       COALESCE(ca.nombre, 'Sin canal')             AS canal,
       COALESCE(c.canal_codigo, 'SIN_CANAL')        AS canal_codigo,
       c.ruta_id,
       COALESCE(r.codigo, '(sin ruta)')             AS ruta_codigo,
       COALESCE(r.nombre, 'Sin ruta')               AS ruta_nombre,
       c.secuencia,
       c.permite_credito,
       c.limite_credito,
       c.dias_credito,
       c.bloqueado,
       c.codigo_postal,
       c.creado_en::date                            AS fecha_alta,
       -- Un cliente levantado en la calle por un vendedor, contra uno capturado
       -- en la oficina. La diferencia explica por qué a unos les falta canal.
       (c.codigo IS NULL)                           AS es_prospecto
  FROM clientes c
  LEFT JOIN canales ca ON ca.codigo = c.canal_codigo
  LEFT JOIN rutas   r  ON r.id      = c.ruta_id
 WHERE c.estatus <> 'baja'
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_cliente ON dim_cliente (cliente_id);
CREATE INDEX IF NOT EXISTS idx_dim_cliente_ruta  ON dim_cliente (ruta_id);
CREATE INDEX IF NOT EXISTS idx_dim_cliente_canal ON dim_cliente (canal_codigo);


CREATE MATERIALIZED VIEW IF NOT EXISTS dim_producto AS
SELECT p.id                                   AS producto_id,
       p.sku,
       p.nombre,
       COALESCE(cat.nombre, 'Sin categoría')   AS categoria,
       COALESCE(m.nombre,   'Sin marca')       AS marca,
       p.unidad_base,
       p.tasa_iva,
       p.activo,
       -- Cuántas unidades base trae la presentación más grande. Es lo que
       -- permite leer "480 piezas" como "20 cajas" en un reporte de rotación,
       -- que es como lo piensa quien compra.
       (SELECT max(u.factor) FROM producto_unidades u
         WHERE u.producto_id = p.id)           AS factor_maximo,
       (SELECT u.unidad_codigo FROM producto_unidades u
         WHERE u.producto_id = p.id ORDER BY u.factor DESC LIMIT 1) AS unidad_mayor
  FROM productos p
  LEFT JOIN categorias cat ON cat.id = p.categoria_id
  LEFT JOIN marcas     m   ON m.id   = p.marca_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_producto ON dim_producto (producto_id);
CREATE INDEX IF NOT EXISTS idx_dim_producto_cat   ON dim_producto (categoria);


CREATE MATERIALIZED VIEW IF NOT EXISTS dim_vendedor AS
SELECT u.id                                   AS vendedor_id,
       u.codigo,
       u.nombre,
       u.rol_codigo,
       u.activo,
       u.almacen_id,
       COALESCE(a.codigo, '(sin camión)')      AS camion_codigo,
       -- Las rutas que tiene asignadas, no la que dice `rutas.vendedor_id`: es
       -- `usuarios_rutas` lo que decide qué datos viajan a su teléfono, y por
       -- tanto qué puede haber vendido.
       COALESCE(
           (SELECT string_agg(r.codigo, ', ' ORDER BY r.codigo)
              FROM usuarios_rutas ur JOIN rutas r ON r.id = ur.ruta_id
             WHERE ur.usuario_id = u.id),
           '(sin ruta)'
       )                                       AS rutas,
       u.creado_en::date                       AS fecha_alta
  FROM usuarios u
  LEFT JOIN almacenes a ON a.id = u.almacen_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_vendedor ON dim_vendedor (vendedor_id);


CREATE MATERIALIZED VIEW IF NOT EXISTS dim_ruta AS
SELECT r.id                                   AS ruta_id,
       r.codigo,
       r.nombre,
       r.activo,
       r.vendedor_id                           AS titular_id,
       COALESCE(u.nombre, 'Sin titular')       AS titular,
       (SELECT count(*) FROM clientes c
         WHERE c.ruta_id = r.id AND c.estatus = 'activo') AS clientes_activos
  FROM rutas r
  LEFT JOIN usuarios u ON u.id = r.vendedor_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_ruta ON dim_ruta (ruta_id);


-- ===========================================================================
-- fact_ventas — grano: UNA PARTIDA
-- ===========================================================================
-- El grano más fino que sirve, y la razón es la rotación: para saber cuántas
-- piezas de atún se movieron hace falta la línea, no el total del ticket.
--
-- `cantidad_base` y no `cantidad`: la primera está en unidad base siempre, la
-- segunda está en la unidad que se capturó. Sumar `cantidad` mezclaría cajas con
-- piezas y daría un número sin significado — es el mismo descuadre caja/pieza
-- que el resto del sistema evita escribiendo SIEMPRE en unidad base.
--
-- Solo ventas `confirmada`: una cancelada no es venta. Y se conserva
-- `requiere_revision` para poder preguntar "¿cuánto de este mes está marcado?",
-- que es la pregunta que decide si una cifra se puede usar.
CREATE MATERIALIZED VIEW IF NOT EXISTS fact_ventas AS
SELECT vp.id                                   AS partida_id,
       v.id                                    AS venta_id,
       v.fecha_operativa                       AS fecha,
       v.cliente_id,
       v.vendedor_id,
       v.ruta_id,
       vp.producto_id,
       v.tipo,                                 -- contado | credito
       vp.unidad_codigo,
       vp.cantidad,                            -- como se capturó
       vp.cantidad_base,                       -- SIEMPRE comparable
       vp.precio_unitario,
       vp.importe,
       vp.tasa_iva,
       v.visita_id,
       v.requiere_revision,
       -- Cuánto tardó en llegar. Es la medida de la incompletitud del pasado:
       -- si el promedio sube, hay equipos sincronizando tarde y los cierres de
       -- esos días se calcularon sobre menos ventas de las que hubo.
       GREATEST(0, (v.fecha_servidor::date - v.fecha_operativa))::smallint
                                               AS dias_en_llegar
  FROM venta_partidas vp
  JOIN ventas v ON v.id = vp.venta_id
 WHERE v.estado = 'confirmada'
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_ventas          ON fact_ventas (partida_id);
CREATE INDEX IF NOT EXISTS idx_fact_ventas_fecha          ON fact_ventas (fecha);
CREATE INDEX IF NOT EXISTS idx_fact_ventas_producto_fecha ON fact_ventas (producto_id, fecha);
CREATE INDEX IF NOT EXISTS idx_fact_ventas_cliente_fecha  ON fact_ventas (cliente_id, fecha);
CREATE INDEX IF NOT EXISTS idx_fact_ventas_vendedor_fecha ON fact_ventas (vendedor_id, fecha);


-- ===========================================================================
-- fact_visitas — grano: (VENDEDOR, CLIENTE, DÍA)
-- ===========================================================================
-- La tabla más importante del laboratorio, y la que más fácil se construye mal.
--
-- **Una visita es un cliente visitado en un día, no un documento.** Si un pedido
-- se partió en dos remisiones, es UNA visita. Contar documentos infla la
-- efectividad y desinfla el drop size, y premia al vendedor que parte un pedido
-- en dos — exactamente al revés de lo que se quiere medir.
--
-- Los dos desenlaces posibles de pararse frente a una tienda son la venta y el
-- no-drop, así que el `UNION ALL` de los dos ES el universo de visitas. Un JOIN
-- entre ventas y no-drops dejaría fuera al vendedor que le vendió a todos, por
-- haber tenido un día perfecto.
--
-- Cuando un cliente tiene no-drop Y venta el mismo día —se volvió a pasar y a la
-- segunda compró— la visita cuenta como vendida, porque el desenlace fue la
-- venta; pero su no-drop se conserva en `motivos_no_drop` para no perder la
-- causa de la primera pasada.
CREATE MATERIALIZED VIEW IF NOT EXISTS fact_visitas AS
WITH eventos AS (
    SELECT v.vendedor_id, v.cliente_id, v.fecha_operativa AS fecha,
           v.total AS importe, 1 AS vendio, 0 AS no_vendio,
           NULL::text AS motivo_no_drop, v.tipo
      FROM ventas v
     WHERE v.estado = 'confirmada'
    UNION ALL
    SELECT n.vendedor_id, n.cliente_id, n.fecha_operativa,
           0::numeric, 0, 1,
           n.motivo_codigo, NULL::text
      FROM no_drops n
)
SELECT e.vendedor_id,
       e.cliente_id,
       e.fecha,
       -- El desenlace de la visita.
       (sum(e.vendio) > 0)                           AS vendio,
       sum(e.importe)::numeric(14,2)                 AS importe,
       -- Documentos, para poder ver la diferencia con las visitas. Que no
       -- coincidan no es un error: es un pedido partido en dos remisiones, o un
       -- cliente al que se volvió a pasar.
       sum(e.vendio)::smallint                       AS documentos_venta,
       sum(e.no_vendio)::smallint                    AS documentos_no_drop,
       array_remove(array_agg(DISTINCT e.motivo_no_drop), NULL) AS motivos_no_drop,
       -- Si alguna de las ventas del día fue a crédito.
       bool_or(e.tipo = 'credito')                   AS hubo_credito
  FROM eventos e
 GROUP BY e.vendedor_id, e.cliente_id, e.fecha
WITH DATA;

-- La llave natural ES el grano. Si este índice fallara por duplicados, la
-- definición de visita estaría mal y todo lo que cuelga de ella también.
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_visitas
    ON fact_visitas (vendedor_id, cliente_id, fecha);
CREATE INDEX IF NOT EXISTS idx_fact_visitas_fecha    ON fact_visitas (fecha);
CREATE INDEX IF NOT EXISTS idx_fact_visitas_cliente  ON fact_visitas (cliente_id, fecha);
CREATE INDEX IF NOT EXISTS idx_fact_visitas_vendedor ON fact_visitas (vendedor_id, fecha);

COMMENT ON MATERIALIZED VIEW fact_visitas IS
    'Grano: (vendedor, cliente, día). Una visita es un cliente visitado en un '
    'día, NO un documento: dos remisiones al mismo cliente el mismo día son una '
    'visita. De esta definición dependen el drop size y la efectividad.';


-- ===========================================================================
-- fact_movimientos — el libro mayor, listo para preguntar
-- ===========================================================================
-- `movimientos_inventario` guarda `cantidad > 0` siempre y codifica la dirección
-- en origen/destino. Eso es correcto para un libro contable —no hay cantidades
-- negativas que interpretar— pero obliga a razonar el signo en cada consulta.
--
-- Aquí se resuelve una vez: un renglón por almacén afectado, con el signo ya
-- aplicado. Sumar `delta` de un almacén hasta una fecha da su existencia en esa
-- fecha, **reconstruida**. Es lo que permite medir rotación histórica sin haber
-- guardado fotos diarias del inventario: el libro es append-only, así que el
-- pasado es recalculable.
CREATE MATERIALIZED VIEW IF NOT EXISTS fact_movimientos AS
SELECT mi.id                           AS movimiento_id,
       'salida'::text                  AS direccion,
       mi.almacen_origen_id            AS almacen_id,
       mi.producto_id,
       mi.tipo,
       COALESCE(mi.fecha_dispositivo::date, mi.fecha_servidor::date) AS fecha,
       -mi.cantidad                    AS delta,
       mi.cantidad                     AS cantidad_abs,
       mi.documento_tipo,
       mi.documento_id,
       mi.usuario_id
  FROM movimientos_inventario mi
 WHERE mi.almacen_origen_id IS NOT NULL
UNION ALL
SELECT mi.id,
       'entrada',
       mi.almacen_destino_id,
       mi.producto_id,
       mi.tipo,
       COALESCE(mi.fecha_dispositivo::date, mi.fecha_servidor::date),
       mi.cantidad,
       mi.cantidad,
       mi.documento_tipo,
       mi.documento_id,
       mi.usuario_id
  FROM movimientos_inventario mi
 WHERE mi.almacen_destino_id IS NOT NULL
WITH DATA;

-- (movimiento_id, direccion) y no movimiento_id: un traspaso camión→camión
-- produce DOS renglones del mismo movimiento, y ése es justo el caso que el
-- índice tiene que admitir.
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_movimientos
    ON fact_movimientos (movimiento_id, direccion);
CREATE INDEX IF NOT EXISTS idx_fact_mov_almacen_prod
    ON fact_movimientos (almacen_id, producto_id, fecha);
CREATE INDEX IF NOT EXISTS idx_fact_mov_fecha ON fact_movimientos (fecha);
