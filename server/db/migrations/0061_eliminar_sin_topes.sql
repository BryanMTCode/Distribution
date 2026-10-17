-- =============================================================================
-- 0061 · Eliminar sin topes: el corte también se deshace
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §126): «desde el dashboard
-- quiero tener poder absoluto de eliminar lo que yo quiera y que se anulen los
-- efectos… principalmente cargas, ventas, movimientos del almacén, cortes
-- incluso». Para probar con el almacén y luego borrar lo de prueba.
--
-- Hasta aquí el corte no se anulaba (§119). Ahora sí, y deshacer un corte
-- cerrado deja huella en un libro que no se borra: lo que se le cargó al
-- vendedor (`cuenta_vendedor`, inmutable, migración 0039) se compensa con una
-- condonación que apunta a ese corte. Por eso el corte anulado NO se borra: se
-- queda como «anulada», y la carga puede volver a cortarse con otro.
--
--   · `liquidaciones.estado` admite 'anulada', y una carga tiene UN corte vivo
--     (el anulado no cuenta).
--   · `cortes_vendedor.estado` admite 'anulado': el corte que mandó el
--     vendedor de una carga que se eliminó.
--   · `anulaciones.tipo` admite 'corte'.
--   · El delta de la carga liquidada lleva los ajustes del corte VIVO, no los
--     del anulado.
-- =============================================================================

ALTER TABLE liquidaciones DROP CONSTRAINT IF EXISTS liquidaciones_estado_check;
ALTER TABLE liquidaciones
    ADD CONSTRAINT liquidaciones_estado_check
    CHECK (estado IN ('abierta', 'cuadrada', 'con_diferencia', 'cerrada', 'anulada'));

DROP INDEX IF EXISTS uq_liquidacion_carga;
CREATE UNIQUE INDEX uq_liquidacion_carga
    ON liquidaciones(carga_id) WHERE estado <> 'anulada';

ALTER TABLE cortes_vendedor DROP CONSTRAINT IF EXISTS cortes_vendedor_estado_check;
ALTER TABLE cortes_vendedor
    ADD CONSTRAINT cortes_vendedor_estado_check
    CHECK (estado IN ('pendiente', 'cerrado', 'reemplazado', 'anulado'));

ALTER TABLE anulaciones DROP CONSTRAINT IF EXISTS anulaciones_tipo_check;
ALTER TABLE anulaciones
    ADD CONSTRAINT anulaciones_tipo_check
    CHECK (tipo IN ('venta', 'mayoreo', 'no_venta', 'merma', 'devolucion', 'cambio',
                    'carga', 'devolucion_bodega', 'traspaso', 'entrada', 'salida',
                    'ajuste', 'corte'));

-- -----------------------------------------------------------------------------
-- El delta de la carga, con los ajustes del corte vivo
-- -----------------------------------------------------------------------------
-- Igual que en 0030, con una sola diferencia: `AND l.estado <> 'anulada'`. Sin
-- ella, una carga que se volvió a cortar le mandaría al teléfono los ajustes de
-- los dos cortes juntos.
CREATE OR REPLACE FUNCTION fn_registrar_cambio_carga() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
    v_detalle  jsonb;
    v_ajustes  jsonb;
BEGIN
    IF (v_registro ->> 'estado') = 'borrador' THEN
        RETURN NULL;
    END IF;

    IF TG_OP <> 'DELETE' THEN
        SELECT COALESCE(
                 jsonb_agg(
                   jsonb_build_object(
                     'producto_id', d.producto_id,
                     'cantidad',    d.cantidad::text,
                     'lote',        d.lote,
                     'caducidad',   d.caducidad
                   )
                   ORDER BY d.producto_id, COALESCE(d.lote, '')
                 ),
                 '[]'::jsonb
               )
          INTO v_detalle
          FROM carga_detalle d
         WHERE d.carga_id = (v_registro ->> 'id')::uuid;

        v_registro := v_registro || jsonb_build_object('detalle', v_detalle);

        IF (v_registro ->> 'estado') = 'liquidada' THEN
            SELECT COALESCE(
                     jsonb_agg(
                       jsonb_build_object(
                         'producto_id', ld.producto_id,
                         'cantidad',    ld.diferencia::text
                       )
                       ORDER BY ld.producto_id
                     ),
                     '[]'::jsonb
                   )
              INTO v_ajustes
              FROM liquidacion_detalle ld
              JOIN liquidaciones l ON l.id = ld.liquidacion_id
             WHERE l.carga_id = (v_registro ->> 'id')::uuid
               AND l.estado <> 'anulada'
               AND ld.diferencia <> 0;

            v_registro := v_registro || jsonb_build_object('ajustes', v_ajustes);
        END IF;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, vendedor_id, payload)
    VALUES (
        'carga',
        (v_registro ->> 'id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
        (v_registro ->> 'ruta_id')::uuid,
        (v_registro ->> 'vendedor_id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE v_registro END
    );
    RETURN NULL;
END;
$$;
