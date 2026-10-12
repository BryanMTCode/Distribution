-- =============================================================================
-- 0059 · Operaciones: todas juntas, y anular deshaciendo lo que movieron
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §119): «quiero una parte en el
-- dashboard donde aparezcan todas las operaciones juntas para borrar lo que yo
-- necesite cuando quiera». Y al preguntarle qué es «borrar»: ANULAR Y DESHACER.
-- La mercancía regresa a donde estaba, el dinero sale de las cifras, el teléfono
-- del vendedor se entera, y la operación queda en la lista como «anulada», con
-- quién, cuándo y por qué.
--
-- No se borra nada del libro mayor (`movimientos_inventario` es append-only):
-- lo que deshace un movimiento es otro movimiento al revés, con documento
-- 'anulacion'. Así el inventario sigue cuadrando contra su historia.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. El documento de la anulación
-- -----------------------------------------------------------------------------
-- Uno por operación anulada. Las que tienen estado (venta, merma, carga…) además
-- pasan a 'cancelada'; la visita sin venta no tiene estado y se BORRA, así que
-- este renglón —con lo que decía, en `datos`— es lo único que queda de ella.
CREATE TABLE IF NOT EXISTS anulaciones (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo             text NOT NULL CHECK (tipo IN (
                         'venta', 'mayoreo', 'no_venta', 'merma', 'devolucion',
                         'cambio', 'carga', 'devolucion_bodega', 'traspaso',
                         'entrada', 'salida', 'ajuste')),
    documento_id     uuid NOT NULL,
    folio            text,
    -- Lo que la lista decía de la operación al anularla: cliente, almacén,
    -- piezas. Para la visita sin venta es lo único que la sigue describiendo.
    descripcion      text NOT NULL,
    vendedor_id      uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    importe          numeric(14,2),
    -- El día de la OPERACIÓN, no el de la anulación: así sigue apareciendo en
    -- el periodo donde ocurrió.
    fecha_operativa  date NOT NULL,
    motivo           text NOT NULL CHECK (length(btrim(motivo)) >= 10),
    -- Quién la anuló. Es una firma: sin ON DELETE, igual que las ventas y los
    -- cortes, ese usuario ya no se puede borrar (solo desactivar).
    usuario_id       uuid NOT NULL REFERENCES usuarios(id),
    anulada_en       timestamptz NOT NULL DEFAULT now(),
    -- Lo que se deshizo, para leerlo después: renglones, cantidades, el aviso
    -- de que el corte ya estaba cerrado…
    datos            jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Una operación se anula UNA vez. Un doble clic no deshace dos veces.
    CONSTRAINT uq_anulacion_documento UNIQUE (tipo, documento_id)
);

CREATE INDEX IF NOT EXISTS idx_anulaciones_fecha ON anulaciones(fecha_operativa DESC);

COMMENT ON TABLE anulaciones IS
    'Operaciones anuladas desde Administración → Operaciones: quién, cuándo, por '
    'qué y qué se deshizo. ADR 0002 §119.';

-- -----------------------------------------------------------------------------
-- 2. El ajuste del camión que le avisa al teléfono
-- -----------------------------------------------------------------------------
-- Una merma, una devolución del cliente o una devolución a bodega movieron el
-- camión, y el teléfono ya lo descontó (o lo sumó) por su cuenta. Al anularlas,
-- el único canal que el teléfono ya sabe aplicar para «súmale esto a tu camión»
-- es el ajuste del camión (migración 0032): delta firmado, aplicado una sola vez.
--
-- Un tipo propio, 'anulacion', y no 'entrada' o 'conteo': no es mercancía nueva
-- ni un conteo, y el signo va en los dos sentidos (anular una merma SUMA,
-- anular una devolución del cliente RESTA). El teléfono no lee el tipo, solo el
-- delta y la nota, así que no necesita versión nueva.
ALTER TABLE ajustes_camion DROP CONSTRAINT IF EXISTS ajustes_camion_tipo_check;
ALTER TABLE ajustes_camion
    ADD CONSTRAINT ajustes_camion_tipo_check
    CHECK (tipo IN ('conteo', 'merma', 'entrada', 'anulacion'));

-- -----------------------------------------------------------------------------
-- 3. El permiso
-- -----------------------------------------------------------------------------
-- Ver la lista es de quien ya ve las ventas de todos; ANULAR es otro permiso,
-- porque deshace el trabajo de otros. No se le concede a ningún rol: el
-- administrador lo tiene por serlo, y dárselo a alguien más es una decisión que
-- se toma con nombre.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('operaciones.anular', 'Anular cualquier operación, deshaciendo lo que movió', 'sistema')
ON CONFLICT DO NOTHING;
