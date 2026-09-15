-- =============================================================================
-- 0006 · Operaciones secundarias: mermas, devoluciones y no-drops
-- =============================================================================
-- Propiedad del dato: DISPOSITIVO. Mismas reglas de idempotencia que ventas.
-- =============================================================================

CREATE TABLE motivos_merma (
    codigo          text PRIMARY KEY,   -- 'CADUCADO','DAÑADO_TRANSPORTE','ROTO'
    nombre          text NOT NULL,
    afecta_vendedor boolean NOT NULL DEFAULT false,  -- ¿se le descuenta?
    activo          boolean NOT NULL DEFAULT true
);

CREATE TABLE mermas (
    id                  uuid PRIMARY KEY,           -- UUID del dispositivo
    dispositivo_id      uuid NOT NULL REFERENCES dispositivos(id),
    folio_consecutivo   integer NOT NULL CHECK (folio_consecutivo > 0),
    folio_local         text NOT NULL,

    tipo                text NOT NULL
                        CHECK (tipo IN ('merma','devolucion_cliente')),
    almacen_id          uuid NOT NULL REFERENCES almacenes(id),
    vendedor_id         uuid NOT NULL REFERENCES usuarios(id),
    -- Solo en devoluciones de cliente.
    cliente_id          uuid REFERENCES clientes(id),
    venta_origen_id     uuid REFERENCES ventas(id),
    visita_id           uuid,

    motivo_codigo       text NOT NULL REFERENCES motivos_merma(codigo),
    observaciones       text,
    -- Evidencia fotográfica: se sube cuando vuelve la señal, no bloquea la
    -- operación offline.
    foto_ruta           text,
    foto_sincronizada   boolean NOT NULL DEFAULT false,

    lat                 numeric(10,7),
    lng                 numeric(10,7),

    estado              text NOT NULL DEFAULT 'confirmada'
                        CHECK (estado IN ('confirmada','cancelada')),
    fecha_dispositivo   timestamptz NOT NULL,
    fecha_servidor      timestamptz NOT NULL DEFAULT now(),
    fecha_operativa     date NOT NULL,
    requiere_revision   boolean NOT NULL DEFAULT false,

    CONSTRAINT devolucion_requiere_cliente
        CHECK (tipo <> 'devolucion_cliente' OR cliente_id IS NOT NULL)
);

CREATE UNIQUE INDEX uq_merma_folio_dispositivo
    ON mermas(dispositivo_id, folio_consecutivo);
CREATE INDEX idx_mermas_operativa ON mermas(fecha_operativa DESC);

CREATE TABLE merma_detalle (
    id              uuid PRIMARY KEY,
    merma_id        uuid NOT NULL REFERENCES mermas(id) ON DELETE CASCADE,
    producto_id     uuid NOT NULL REFERENCES productos(id),
    cantidad_base   numeric(14,3) NOT NULL CHECK (cantidad_base > 0),
    lote            text,
    -- Costo congelado al momento, para valuar la pérdida.
    costo_unitario  numeric(14,4),
    UNIQUE (merma_id, producto_id)
);

-- -----------------------------------------------------------------------------
-- No-drops: visitas sin venta
-- -----------------------------------------------------------------------------
-- El catálogo de motivos es CERRADO a propósito. Texto libre = datos que
-- nunca vas a poder analizar. Si hace falta un motivo nuevo, se agrega aquí
-- y se sincroniza, no se escribe a mano en la calle.
-- -----------------------------------------------------------------------------
CREATE TABLE motivos_no_drop (
    codigo              text PRIMARY KEY,
    nombre              text NOT NULL,
    -- Clasificación para el análisis: ¿la culpa es del cliente, del vendedor
    -- o de la operación?
    categoria           text NOT NULL
                        CHECK (categoria IN ('cliente','operacion','producto','vendedor')),
    -- Motivos que exigen comentario adicional del vendedor.
    requiere_nota       boolean NOT NULL DEFAULT false,
    orden               smallint NOT NULL DEFAULT 0,
    activo              boolean NOT NULL DEFAULT true
);

COMMENT ON TABLE motivos_no_drop IS
    'Catálogo cerrado. Ejemplos: CERRADO, NO_HAY_DINERO, TIENE_INVENTARIO, '
    'DUEÑO_AUSENTE, SIN_CREDITO, NO_LE_INTERESA, PRODUCTO_AGOTADO_CAMION.';

CREATE TABLE no_drops (
    id                  uuid PRIMARY KEY,           -- UUID del dispositivo
    dispositivo_id      uuid NOT NULL REFERENCES dispositivos(id),
    folio_consecutivo   integer NOT NULL CHECK (folio_consecutivo > 0),

    cliente_id          uuid NOT NULL REFERENCES clientes(id),
    vendedor_id         uuid NOT NULL REFERENCES usuarios(id),
    ruta_id             uuid REFERENCES rutas(id),
    visita_id           uuid,

    motivo_codigo       text NOT NULL REFERENCES motivos_no_drop(codigo),
    nota                text,

    -- ------------------------------------------------------------------
    -- GPS obligatorio: es la prueba de que la visita ocurrió. Un no-drop sin
    -- coordenadas es indistinguible de una visita que nunca se hizo.
    -- ------------------------------------------------------------------
    lat                     numeric(10,7) NOT NULL,
    lng                     numeric(10,7) NOT NULL,
    ubicacion               geography(Point,4326)
                            GENERATED ALWAYS AS (
                                ST_SetSRID(ST_MakePoint(lng::float8, lat::float8), 4326)::geography
                            ) STORED,
    ubicacion_precision_m   numeric(8,2),
    ubicacion_origen        text NOT NULL DEFAULT 'gps'
                            CHECK (ubicacion_origen IN ('gps','manual')),
    distancia_cliente_m     numeric(10,2),

    fecha_dispositivo   timestamptz NOT NULL,
    fecha_servidor      timestamptz NOT NULL DEFAULT now(),
    fecha_operativa     date NOT NULL,
    requiere_revision   boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX uq_nodrop_folio_dispositivo
    ON no_drops(dispositivo_id, folio_consecutivo);
CREATE INDEX idx_nodrops_cliente   ON no_drops(cliente_id, fecha_operativa DESC);
CREATE INDEX idx_nodrops_operativa ON no_drops(fecha_operativa DESC);
CREATE INDEX idx_nodrops_ubicacion ON no_drops USING gist (ubicacion);
