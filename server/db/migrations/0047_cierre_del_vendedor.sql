-- =============================================================================
-- 0047 · El cierre del vendedor: corte, solicitud de carga y su aceptación
-- =============================================================================
-- Retroalimentación del piloto (octubre 2026, ADR 0002 §82). El fin del día es:
--
--   1. El vendedor hace su «Corte del día» en el teléfono: cuenta lo que le
--      sobró arriba del camión y el efectivo que entrega. Sale un ticket.
--   2. Inmediatamente después pide la carga para el día siguiente.
--   3. La solicitud viaja al gerente, que la revisa junto con el corte y la
--      acepta: el corte se cierra (lo que falte va a la cuenta del vendedor) y
--      se crea y confirma la carga de mañana. Sale el ticket de la carga.
--
-- Solo se hace UNA carga al día, y es para el día siguiente.
--
-- El corte y la solicitud nacen en el teléfono SIN señal —se mandan como
-- operaciones de la cola, con el id que les dio el teléfono— así que la ingesta
-- es idempotente por id, como la venta. Lo que decide (cerrar el corte, mover
-- el inventario) lo hace la oficina al aceptar, con las mismas funciones que el
-- corte y la carga de siempre.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- El corte que declara el vendedor
-- -----------------------------------------------------------------------------
-- Es SU palabra: cuánto le sobró y cuánto efectivo entrega. No mueve nada al
-- llegar. Lo que mueve inventario y dinero es la liquidación que se cierra al
-- aceptarlo, y esa ya existe (`liquidaciones`): aquí solo se guarda lo que dijo
-- y a qué liquidación terminó dando lugar.
CREATE TABLE IF NOT EXISTS cortes_vendedor (
    id                  uuid PRIMARY KEY,           -- el que le dio el teléfono
    vendedor_id         uuid NOT NULL REFERENCES usuarios(id),
    dispositivo_id      uuid REFERENCES dispositivos(id),
    carga_id            uuid REFERENCES cargas(id),
    fecha_operativa     date NOT NULL,
    efectivo_declarado  numeric(14,2) NOT NULL CHECK (efectivo_declarado >= 0),
    observaciones       text,
    fecha_dispositivo   timestamptz,
    recibido_en         timestamptz NOT NULL DEFAULT now(),
    -- 'reemplazado': el vendedor lo volvió a hacer ese mismo día antes de que
    -- la oficina lo viera. Vale el último.
    estado              text NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'cerrado', 'reemplazado')),
    liquidacion_id      uuid REFERENCES liquidaciones(id),
    resuelto_por        uuid REFERENCES usuarios(id),
    resuelto_en         timestamptz,
    nota                text
);

CREATE INDEX IF NOT EXISTS idx_cortes_vendedor_pendientes
    ON cortes_vendedor(vendedor_id, fecha_operativa) WHERE estado = 'pendiente';

-- Lo contado arriba del camión, en unidad base. El producto que no viene es
-- un producto que el vendedor no tiene: al cerrar vale cero, como en el panel.
CREATE TABLE IF NOT EXISTS corte_vendedor_conteo (
    corte_id     uuid NOT NULL REFERENCES cortes_vendedor(id) ON DELETE CASCADE,
    producto_id  uuid NOT NULL REFERENCES productos(id),
    cantidad     numeric(14,3) NOT NULL CHECK (cantidad >= 0),
    PRIMARY KEY (corte_id, producto_id)
);

-- -----------------------------------------------------------------------------
-- La solicitud de carga para el día siguiente
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS solicitudes_carga (
    id                  uuid PRIMARY KEY,           -- el que le dio el teléfono
    vendedor_id         uuid NOT NULL REFERENCES usuarios(id),
    dispositivo_id      uuid REFERENCES dispositivos(id),
    corte_id            uuid REFERENCES cortes_vendedor(id),
    -- PARA cuándo: el día siguiente al corte. La carga que se cree lleva esta
    -- misma fecha.
    fecha_operativa     date NOT NULL,
    observaciones       text,
    fecha_dispositivo   timestamptz,
    recibido_en         timestamptz NOT NULL DEFAULT now(),
    estado              text NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN ('pendiente', 'aceptada', 'rechazada',
                                          'reemplazada')),
    carga_id            uuid REFERENCES cargas(id),
    resuelta_por        uuid REFERENCES usuarios(id),
    resuelta_en         timestamptz,
    motivo              text
);

-- Una carga al día: a lo más UNA solicitud aceptada por vendedor y fecha.
CREATE UNIQUE INDEX IF NOT EXISTS uq_solicitud_aceptada_por_dia
    ON solicitudes_carga(vendedor_id, fecha_operativa) WHERE estado = 'aceptada';
CREATE INDEX IF NOT EXISTS idx_solicitudes_carga_pendientes
    ON solicitudes_carga(vendedor_id, fecha_operativa) WHERE estado = 'pendiente';

-- Lo que pidió, en la presentación en que lo pidió (cajas, casi siempre) y ya
-- convertido a unidad base. `cantidad_aceptada` es lo que el gerente dejó: si
-- la bodega no tiene, la baja; si no la toca, es lo pedido.
CREATE TABLE IF NOT EXISTS solicitud_carga_detalle (
    solicitud_id       uuid NOT NULL REFERENCES solicitudes_carga(id) ON DELETE CASCADE,
    producto_id        uuid NOT NULL REFERENCES productos(id),
    unidad_codigo      text NOT NULL,
    bultos             numeric(14,3) NOT NULL CHECK (bultos > 0),
    cantidad           numeric(14,3) NOT NULL CHECK (cantidad > 0),
    cantidad_aceptada  numeric(14,3) CHECK (cantidad_aceptada IS NULL OR cantidad_aceptada >= 0),
    PRIMARY KEY (solicitud_id, producto_id)
);

-- -----------------------------------------------------------------------------
-- El delta hacia el teléfono: «tu carga de mañana se aceptó»
-- -----------------------------------------------------------------------------
-- Como el traspaso (0036): el documento nace en el teléfono, así que el INSERT
-- en 'pendiente' no se publica —sería un eco—. Se publica cuando cambia su
-- estado, y también el INSERT que ya llega resuelto (la ingesta la rechaza al
-- llegar si ese día ya tenía una carga aceptada). Acotado al vendedor.
CREATE OR REPLACE FUNCTION fn_registrar_cambio_solicitud_carga() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_detalle jsonb;
    v_folio   text;
BEGIN
    IF TG_OP = 'INSERT' AND NEW.estado = 'pendiente' THEN
        RETURN NULL;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.estado IS NOT DISTINCT FROM NEW.estado
       AND OLD.carga_id IS NOT DISTINCT FROM NEW.carga_id THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(
             jsonb_agg(
               jsonb_build_object(
                 'producto_id',       d.producto_id,
                 'unidad_codigo',     d.unidad_codigo,
                 -- Cantidades como TEXTO con sus tres decimales (contracts §1.4).
                 'bultos',            d.bultos::text,
                 'cantidad',          d.cantidad::text,
                 'cantidad_aceptada', d.cantidad_aceptada::text
               )
               ORDER BY d.producto_id
             ),
             '[]'::jsonb
           )
      INTO v_detalle
      FROM solicitud_carga_detalle d
     WHERE d.solicitud_id = NEW.id;

    SELECT folio INTO v_folio FROM cargas WHERE id = NEW.carga_id;

    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    VALUES (
        'solicitud_carga',
        NEW.id,
        'upsert',
        NEW.vendedor_id,
        jsonb_build_object(
            'id',              NEW.id,
            'corte_id',        NEW.corte_id,
            'fecha_operativa', NEW.fecha_operativa,
            'estado',          NEW.estado,
            'carga_id',        NEW.carga_id,
            'carga_folio',     v_folio,
            'resuelta_en',     NEW.resuelta_en,
            'motivo',          NEW.motivo,
            'detalle',         v_detalle
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_cambio_solicitud_carga ON solicitudes_carga;
CREATE TRIGGER trg_cambio_solicitud_carga
    AFTER INSERT OR UPDATE ON solicitudes_carga
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_solicitud_carga();

-- -----------------------------------------------------------------------------
-- El gerente acepta: necesita cargar (0044) y cortar (0045). Ya los tiene.
-- -----------------------------------------------------------------------------
