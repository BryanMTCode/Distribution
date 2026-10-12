-- =============================================================================
-- 0057 · El objetivo diario fijo: «por día 3,500, hasta que yo lo modifique»
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §110): poner el objetivo de
-- cada día una sola vez, y que valga hasta que se cambie. Los objetivos de
-- periodo (0055) se acaban en su fecha; este no tiene fin.
--
-- Cada renglón es un CAMBIO: «desde tal día, tanto cada día, estos días de la
-- semana». El objetivo de un día es el del último renglón que ya había
-- empezado, así que cambiar la cifra no reescribe lo que ya se midió. Un
-- renglón sin monto es «sin objetivo diario desde ese día» (quitarlo).
--
-- `dias_semana` (ISO: 1 = lunes … 7 = domingo) es lo que evita lo que 0021
-- temía de un objetivo diario: que el domingo pinte rojo una ruta que no sale
-- ese día. Por omisión, de lunes a sábado.
-- =============================================================================

CREATE TABLE IF NOT EXISTS objetivos_diarios (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nulo = toda la empresa (que cuenta también el mayoreo).
    ruta_id           uuid REFERENCES rutas(id) ON DELETE CASCADE,
    desde             date NOT NULL,
    -- Nulo = desde este día ya no hay objetivo diario.
    objetivo_venta    numeric(14,2) CHECK (objetivo_venta IS NULL OR objetivo_venta > 0),
    objetivo_visitas  integer CHECK (objetivo_visitas IS NULL OR objetivo_visitas > 0),
    dias_semana       smallint[] NOT NULL DEFAULT '{1,2,3,4,5,6}',
    nota              text,
    fijado_por        uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT objetivo_diario_dias_validos
        CHECK (cardinality(dias_semana) > 0
               AND dias_semana <@ CAST('{1,2,3,4,5,6,7}' AS smallint[])),
    -- Sin monto no hay meta de visitas que medir.
    CONSTRAINT objetivo_diario_visitas_con_monto
        CHECK (objetivo_venta IS NOT NULL OR objetivo_visitas IS NULL),
    -- Un cambio por día y por dueño: guardar dos veces el mismo día lo corrige.
    CONSTRAINT objetivo_diario_un_cambio_por_dia UNIQUE NULLS NOT DISTINCT (ruta_id, desde)
);

COMMENT ON TABLE objetivos_diarios IS
    'El objetivo diario fijo, por ruta o de toda la empresa (ruta_id nulo): cada '
    'renglón vale desde su fecha hasta el siguiente cambio; sin monto = quitado. '
    'ADR 0002 §110.';
