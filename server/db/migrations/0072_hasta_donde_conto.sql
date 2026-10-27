-- =============================================================================
-- 0072 · Hasta qué día contó cada corte
-- =============================================================================
-- «¿Por qué con un vendedor me aparece en su corte que se cargaron 2,389 a la
-- cuenta del vendedor?» (octubre 2026, ADR 0002 §181).
--
-- El corte de una carga cuenta desde su día hasta antes de la siguiente carga,
-- y hacia atrás los días sueltos desde la carga anterior ya cortada (§176). Lo
-- de hacia atrás arrancaba el día siguiente a la FECHA de la anterior, y eso
-- solo es cierto si la anterior contó nada más su día. La carga del sábado quedó
-- «para el domingo» y se vendió el lunes; su corte del lunes contó el lunes. La
-- siguiente fue del martes, y su corte arrancó el lunes otra vez: esperaba lo
-- del lunes, ya entregado, más lo del martes, y el lunes se le cargó al
-- vendedor como faltante de efectivo.
--
-- Ahora cada corte guarda hasta qué día contó, y el siguiente arranca el día
-- después. Se llena al cerrar: el del vendedor, con el día de su corte; el de la
-- oficina, hasta antes de la siguiente carga sin pasar del día en que se cerró.
-- =============================================================================

ALTER TABLE liquidaciones ADD COLUMN IF NOT EXISTS cuenta_hasta date;

COMMENT ON COLUMN liquidaciones.cuenta_hasta IS
    'El último día que cuenta este corte. El corte de la carga siguiente arranca '
    'el día después (ADR 0002 §181). Nulo mientras la oficina no lo cierra.';

-- -----------------------------------------------------------------------------
-- Los que ya se cerraron
-- -----------------------------------------------------------------------------
-- Los que cerró el corte del vendedor: el día de ese corte. No el corte que
-- solo se ató a uno que la oficina ya había cerrado: ése no lo contó.
UPDATE liquidaciones l
   SET cuenta_hasta = k.dia
  FROM (SELECT liquidacion_id, max(fecha_operativa) AS dia
          FROM cortes_vendedor
         WHERE estado = 'cerrado' AND liquidacion_id IS NOT NULL
           AND COALESCE(nota, '') NOT LIKE '%la oficina ya había cortado%'
         GROUP BY liquidacion_id) k
 WHERE k.liquidacion_id = l.id
   AND l.estado = 'cerrada'
   AND l.cuenta_hasta IS NULL;

-- Los de la oficina: hasta antes de la siguiente carga, sin pasar del día en que
-- se cerraron.
UPDATE liquidaciones l
   SET cuenta_hasta = LEAST(
           (SELECT min(s.fecha_operativa) FROM cargas s
             WHERE s.vendedor_id = c.vendedor_id
               AND s.fecha_operativa > c.fecha_operativa
               AND s.estado IN ('confirmada', 'en_ruta', 'liquidada')) - 1,
           (l.cerrada_en AT TIME ZONE 'America/Mazatlan')::date)
  FROM cargas c
 WHERE c.id = l.carga_id
   AND l.estado = 'cerrada'
   AND l.cuenta_hasta IS NULL;
