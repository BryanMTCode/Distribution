-- =============================================================================
-- 0064 · Mover mercancía de la bodega al camión, y de regreso, desde el panel
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §135): «quiero en el dashboard
-- poder mover mercancía a los camiones y viceversa».
--
-- El camión tiene dueño (§0.2): su teléfono vende sin señal con su propio
-- inventario, así que la oficina no le escribe existencias. Lo que sí le llega
-- es una DIFERENCIA —«súmale 5»—, que es como viaja el ajuste del camión
-- (0032, 0037): el teléfono la suma a lo que tenga, haya vendido o no en medio.
--
-- Así se mueve: dos ajustes atados, uno que baja en el origen y otro que sube en
-- el destino, de tipo `traspaso`. El del camión viaja a su teléfono por el
-- disparador de siempre; el de la bodega no viaja a ninguno.
-- =============================================================================

ALTER TABLE ajustes_camion DROP CONSTRAINT IF EXISTS ajustes_camion_tipo_check;
ALTER TABLE ajustes_camion
    ADD CONSTRAINT ajustes_camion_tipo_check
    CHECK (tipo IN ('conteo', 'merma', 'entrada', 'anulacion', 'traspaso'));

-- Las dos mitades van atadas: la que entra apunta a la que sale. Operaciones
-- (§119) muestra la que sale y, al anularla, deshace las dos; anular solo una
-- dejaría mercancía duplicada o desaparecida.
ALTER TABLE ajustes_camion
    ADD COLUMN IF NOT EXISTS pareja_id uuid REFERENCES ajustes_camion (id);

CREATE INDEX IF NOT EXISTS ix_ajustes_camion_pareja
    ON ajustes_camion (pareja_id) WHERE pareja_id IS NOT NULL;

COMMENT ON COLUMN ajustes_camion.pareja_id IS
    'En un traspaso bodega↔camión (§135), la mitad que entra apunta a la que sale.';
