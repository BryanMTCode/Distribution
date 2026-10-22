-- =============================================================================
-- 0069 · Las tiendas que no son del vendedor se van de su teléfono
-- =============================================================================
-- Reporte de la dirección (octubre 2026, ADR 0002 §174): «me aparecen 90 y
-- tantas para el que trae la ruta 1107». El jueves de la 1107 son 47. Y el caso
-- de Luis (§165): le vendió a una tienda que su teléfono enseñaba como suya y
-- que en el servidor era de otra ruta.
--
-- El teléfono no borra tiendas por su cuenta: solo las suelta cuando el
-- servidor le manda la baja. Había tres caminos por los que esa baja no llegaba
-- nunca, y la tienda se le quedaba para siempre:
--
-- 1. **La tienda sin ruta que recibe ruta.** Sin ruta le llega a TODOS los
--    teléfonos (0007). Al darle ruta, `fn_registrar_cambio_cliente` (0034) solo
--    avisaba a la ruta que la perdía «si tenía ruta», así que nadie les decía a
--    los demás que ya no era suya. Así se quedaban las tiendas que el panel dejó
--    sin ruta al guardarlas con la ruta inactiva (§166) y las que se cargaron
--    sin ruta y la recibieron después.
-- 2. **El cliente de mayoreo.** Es de la oficina y va sin ruta (0056 dice «sin
--    ruta no viaja a ningún teléfono»), pero sin ruta es justo lo que viaja a
--    todos: salía en «Todos» de cada vendedor.
-- 3. **El teléfono que se vincula de nuevo.** Se queda con lo que tenía de su
--    vínculo anterior —otro vendedor, otras rutas; los de prueba que se fueron
--    con «empezar a operar»— y las bajas de esas rutas se le mandaban al usuario
--    de antes, no a él.
--
-- Lo que cambia:
--
-- · La tienda que pierde su ruta avisa SIEMPRE: si no tenía ruta, el aviso va a
--   todos (ruta_id NULL). El teléfono que la gana recibe la baja y justo detrás
--   el alta, en el mismo orden del cursor, y se queda con ella.
-- · El cliente de mayoreo sin ruta se publica como baja: el teléfono que lo
--   tenía lo suelta y el que no, no lo recibe.
-- · `fn_soltar_tiendas_ajenas(usuario)` le manda a ese usuario la baja de cada
--   tienda que no es de sus rutas, de los clientes de mayoreo y de las tiendas
--   que ya no existen. Corre al registrar un teléfono (o al pasarlo a otro
--   usuario), y una vez aquí para todos los que tienen teléfono: así se limpian
--   los teléfonos que ya traen tiendas de más, sin APK nuevo. La baja de una
--   tienda que el teléfono no tiene no le hace nada (0034: `UPDATE … activo = 0`).
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_registrar_cambio_cliente() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
BEGIN
    -- La geografía generada se va: el dispositivo ya recibe lat y lng, y el
    -- hexadecimal de PostGIS solo engordaría cada delta.
    v_registro := v_registro - 'ubicacion';

    -- El aviso a quien lo pierde, ANTES del alta en la nueva ruta. Sin ruta lo
    -- tenían todos los teléfonos, así que el aviso va a todos (ruta_id NULL); el
    -- de la ruta nueva aplica la baja y en seguida el alta, y se queda con él.
    IF TG_OP = 'UPDATE' AND OLD.ruta_id IS DISTINCT FROM NEW.ruta_id THEN
        INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
        VALUES ('cliente', OLD.id, 'delete', OLD.ruta_id, NULL);
    END IF;

    -- El cliente de mayoreo sin ruta es de la oficina: a los teléfonos les llega
    -- como baja, para que lo suelte el que ya lo tenía.
    IF TG_OP <> 'DELETE' AND NEW.mayoreo AND NEW.ruta_id IS NULL THEN
        INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
        VALUES ('cliente', NEW.id, 'delete', NULL, NULL);
        RETURN NULL;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
    VALUES (
        'cliente',
        (v_registro ->> 'id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
        (v_registro ->> 'ruta_id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE v_registro END
    );
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_registrar_cambio_cliente() IS
    'Publica los clientes al dispositivo. Cuando la ruta cambia avisa también a '
    'quien PIERDE al cliente —a todos si no tenía ruta—, y el de mayoreo sin ruta '
    'no viaja: si no, esos teléfonos lo visitarían para siempre (ADR 0002 §174).';

CREATE OR REPLACE FUNCTION fn_soltar_tiendas_ajenas(p_usuario uuid) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bajas integer;
BEGIN
    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    SELECT 'cliente', t.id, 'delete', p_usuario, NULL
      FROM (
            -- De una ruta que no es suya.
            SELECT c.id FROM clientes c
             WHERE c.ruta_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM usuarios_rutas ur
                                WHERE ur.usuario_id = p_usuario
                                  AND ur.ruta_id = c.ruta_id)
            UNION
            -- De mayoreo, de la oficina.
            SELECT c.id FROM clientes c WHERE c.ruta_id IS NULL AND c.mayoreo
            UNION
            -- Las que ya no existen: su baja solo le llegó a su ruta de entonces.
            SELECT l.entidad_id FROM change_log l
             WHERE l.entidad = 'cliente'
               AND NOT EXISTS (SELECT 1 FROM clientes c WHERE c.id = l.entidad_id)
           ) t
     ORDER BY t.id;
    GET DIAGNOSTICS v_bajas = ROW_COUNT;
    RETURN v_bajas;
END;
$$;

COMMENT ON FUNCTION fn_soltar_tiendas_ajenas(uuid) IS
    'Le manda a los teléfonos de ese usuario la baja de cada tienda que no es de '
    'sus rutas, de los clientes de mayoreo y de las que ya no existen. Devuelve '
    'cuántas bajas publicó (ADR 0002 §174).';

-- El teléfono que se registra —o que pasa a otro usuario— suelta lo de antes.
CREATE OR REPLACE FUNCTION fn_telefono_nuevo_suelta_lo_ajeno() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM fn_soltar_tiendas_ajenas(NEW.usuario_id);
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_telefono_nuevo_suelta_lo_ajeno() IS
    'Al registrar un teléfono, o al pasarlo a otro usuario, le quita las tiendas '
    'que trajera de su vínculo anterior (ADR 0002 §174).';

DROP TRIGGER IF EXISTS trg_telefono_nuevo_suelta_lo_ajeno ON dispositivos;
CREATE TRIGGER trg_telefono_nuevo_suelta_lo_ajeno
    AFTER INSERT ON dispositivos
    FOR EACH ROW EXECUTE FUNCTION fn_telefono_nuevo_suelta_lo_ajeno();

DROP TRIGGER IF EXISTS trg_telefono_de_otro_suelta_lo_ajeno ON dispositivos;
CREATE TRIGGER trg_telefono_de_otro_suelta_lo_ajeno
    AFTER UPDATE OF usuario_id ON dispositivos
    FOR EACH ROW
    WHEN (OLD.usuario_id IS DISTINCT FROM NEW.usuario_id)
    EXECUTE FUNCTION fn_telefono_nuevo_suelta_lo_ajeno();

-- Una vez, para los teléfonos que ya traen tiendas de más.
DO $$
BEGIN
    PERFORM fn_soltar_tiendas_ajenas(u.usuario_id)
       FROM (SELECT DISTINCT usuario_id FROM dispositivos) u;
END
$$;
