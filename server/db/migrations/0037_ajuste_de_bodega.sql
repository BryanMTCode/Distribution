-- =============================================================================
-- 0037 · El ajuste manual también para bodegas, sin que llegue a ningún teléfono
-- =============================================================================
-- La 0032 hizo el ajuste de inventario para CAMIONES: un documento con folio,
-- nota y asiento en el libro mayor, cuyo delta viaja al teléfono del dueño del
-- camión. La dirección pidió en octubre de 2026 poder ajustar también la bodega
-- desde la misma tabla de inventario («modo dios»).
--
-- El documento sirve igual. Lo que NO sirve tal como está es el disparador, y es
-- el detalle que justifica esta migración:
--
--     SELECT responsable_id INTO v_responsable FROM almacenes WHERE id = ...;
--     INSERT INTO change_log (..., vendedor_id, ...) VALUES (..., v_responsable, ...)
--
-- Una bodega no tiene responsable. `vendedor_id` quedaría NULO, y en el pull un
-- `vendedor_id` nulo significa «para todos» (`vendedor_id IS NULL OR vendedor_id
-- = :usuario`). Cada teléfono de la empresa recibiría el ajuste de la bodega y lo
-- SUMARÍA A SU CAMIÓN. Diez camiones, diez inventarios corrompidos, por una
-- corrección que nadie en la calle debía ver.
--
-- La pantalla va a validar el tipo de almacén, pero la defensa vive aquí: el
-- disparador solo publica cuando el almacén es un CAMIÓN con DUEÑO. Si algún día
-- otro camino inserta un ajuste —un script, otra pantalla—, la regla se cumple
-- igual.
-- =============================================================================

COMMENT ON TABLE ajustes_camion IS
    'Correcciones de la oficina al inventario de un almacén. El nombre es '
    'histórico: nació para camiones (0032) y desde la 0037 también ajusta bodegas. '
    'Solo los de un CAMIÓN viajan al teléfono de su dueño; los de una bodega no '
    'viajan a ninguno. Ver el encabezado de la 0037.';

CREATE OR REPLACE FUNCTION fn_registrar_ajuste_camion() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_responsable uuid;
BEGIN
    SELECT responsable_id INTO v_responsable
      FROM almacenes
     WHERE id = NEW.almacen_id AND tipo = 'camion';

    -- Sin camión con dueño, no hay a quién decírselo. Y NO se publica con
    -- `vendedor_id` nulo, que en el pull significa «a todos los teléfonos».
    IF v_responsable IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    VALUES (
        'ajuste_camion',
        NEW.id,
        'upsert',
        v_responsable,
        jsonb_build_object(
            'id',          NEW.id,
            'folio',       NEW.folio,
            'producto_id', NEW.producto_id,
            'delta',       NEW.delta::text,
            'tipo',        NEW.tipo,
            'nota',        NEW.nota
        )
    );
    RETURN NULL;
END;
$$;
