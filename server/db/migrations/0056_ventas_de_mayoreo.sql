-- =============================================================================
-- 0056 · Ventas de mayoreo de la oficina
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §105): «el administrador puede
-- hacer ventas a mayoreo al precio que él decida, y los clientes él los maneja».
--
-- No es una venta del vendedor y no se mete en `ventas`: esa tabla es la del
-- teléfono —folio del rango del equipo, camión, geocerca, corte del vendedor—, y
-- una venta de la oficina que entrara ahí aparecería en el corte de un vendedor
-- que no la hizo. Ésta sale de una BODEGA, con folio MY- de la oficina, al precio
-- que se escribió en cada renglón (el de la lista se guarda al lado, como
-- referencia), de contado.
--
-- Los clientes de mayoreo son clientes como los demás con `mayoreo = true` y,
-- normalmente, sin ruta: no viajan a ningún teléfono.
-- =============================================================================

ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS mayoreo boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN clientes.mayoreo IS
    'Cliente de mayoreo: lo maneja la oficina y le vende desde la bodega '
    '(ventas_mayoreo). Sin ruta no viaja a ningún teléfono. ADR 0002 §105.';

CREATE SEQUENCE IF NOT EXISTS seq_folio_mayoreo;

CREATE TABLE IF NOT EXISTS ventas_mayoreo (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folio                text NOT NULL UNIQUE,
    cliente_id           uuid NOT NULL REFERENCES clientes(id),
    -- La bodega de donde sale la mercancía.
    almacen_id           uuid NOT NULL REFERENCES almacenes(id),
    -- El día operativo de la venta.
    fecha                date NOT NULL,
    forma_pago           text NOT NULL DEFAULT 'efectivo'
                         CHECK (forma_pago IN ('efectivo', 'transferencia')),
    referencia_pago      text,
    total                numeric(14,2) NOT NULL CHECK (total >= 0),
    estado               text NOT NULL DEFAULT 'confirmada'
                         CHECK (estado IN ('confirmada', 'cancelada')),
    nota                 text,
    vendida_por          uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    creada_en            timestamptz NOT NULL DEFAULT now(),
    cancelada_en         timestamptz,
    cancelada_por        uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    motivo_cancelacion   text,

    CONSTRAINT cancelada_con_motivo
        CHECK (estado <> 'cancelada' OR length(btrim(coalesce(motivo_cancelacion, ''))) >= 3)
);

CREATE INDEX IF NOT EXISTS idx_ventas_mayoreo_fecha ON ventas_mayoreo (fecha DESC);
CREATE INDEX IF NOT EXISTS idx_ventas_mayoreo_cliente ON ventas_mayoreo (cliente_id);

CREATE TABLE IF NOT EXISTS venta_mayoreo_partidas (
    id            bigserial PRIMARY KEY,
    venta_id      uuid NOT NULL REFERENCES ventas_mayoreo(id) ON DELETE CASCADE,
    producto_id   uuid NOT NULL REFERENCES productos(id),
    -- Cómo se vendió: 10 CAJA de 24. El inventario se mueve en piezas.
    unidad_codigo text NOT NULL,
    factor        numeric(14,4) NOT NULL CHECK (factor > 0),
    bultos        numeric(14,3) NOT NULL CHECK (bultos > 0),
    cantidad      numeric(14,3) NOT NULL CHECK (cantidad > 0),
    -- El precio por esa presentación que decidió quien vendió.
    precio        numeric(14,4) NOT NULL CHECK (precio > 0),
    -- El de la lista general en ese momento, para ver el descuento después.
    precio_lista  numeric(14,4),
    importe       numeric(14,2) NOT NULL CHECK (importe >= 0),

    CONSTRAINT piezas_cuadran CHECK (cantidad = bultos * factor)
);

CREATE INDEX IF NOT EXISTS idx_venta_mayoreo_partidas_venta
    ON venta_mayoreo_partidas (venta_id);

COMMENT ON TABLE ventas_mayoreo IS
    'Ventas de mayoreo de la oficina: salen de una bodega, al precio que se '
    'decidió, a un cliente de mayoreo. Folio MY-. ADR 0002 §105.';

INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('mayoreo.vender', 'Vender a mayoreo desde la bodega, al precio que se decida', 'ventas')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('admin', 'mayoreo.vender')
ON CONFLICT DO NOTHING;
