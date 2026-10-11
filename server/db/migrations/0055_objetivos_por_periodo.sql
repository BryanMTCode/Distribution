-- =============================================================================
-- 0055 · Objetivos de cualquier periodo, incluso de un día
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §104): «quiero poder agregar
-- los objetivos en la app; los objetivos pueden ser por el periodo que yo decida,
-- incluso por día».
--
-- `objetivos_ruta` (0021) sigue siendo el objetivo DEL MES de cada ruta: es el
-- que dibuja la barra de avance del tablero, y no se toca. Esta tabla es la de
-- los demás: un día, una semana, una quincena, del 3 al 17… de una ruta o de
-- toda la empresa (`ruta_id` nulo). El avance se calcula al pedirlo, con las
-- ventas de ese rango: no hay modelo de lectura que mantener.
-- =============================================================================

CREATE TABLE IF NOT EXISTS objetivos (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nulo = toda la empresa.
    ruta_id           uuid REFERENCES rutas(id) ON DELETE CASCADE,
    desde             date NOT NULL,
    hasta             date NOT NULL,
    objetivo_venta    numeric(14,2) NOT NULL CHECK (objetivo_venta > 0),
    -- Opcional, como en 0021: una meta de dinero sin meta de visitas es válida;
    -- un 0 significaría «la meta es no visitar a nadie».
    objetivo_visitas  integer CHECK (objetivo_visitas IS NULL OR objetivo_visitas > 0),
    nota              text,
    fijado_por        uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT objetivo_rango_en_orden CHECK (hasta >= desde),
    -- Un año como mucho: lo demás es planeación, no operación.
    CONSTRAINT objetivo_rango_de_un_ano CHECK (hasta - desde <= 366)
);

CREATE INDEX IF NOT EXISTS idx_objetivos_hasta ON objetivos (hasta DESC);

COMMENT ON TABLE objetivos IS
    'Objetivos de cualquier periodo (un día, una semana, un rango), por ruta o de '
    'toda la empresa (ruta_id nulo). El del mes por ruta, el de la barra del '
    'tablero, sigue en objetivos_ruta. ADR 0002 §104.';
