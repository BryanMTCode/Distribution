-- =============================================================================
-- 0033 · Quitar un precio SÍ llega al teléfono
-- =============================================================================
-- Defecto encontrado al construir la edición del catálogo para gerencia, y es de
-- los que no se ven nunca desde el panel: la pantalla dice «se quitó el precio de
-- CAJA: el vendedor ya no la verá», y **el vendedor la sigue viendo para siempre**.
--
-- La cadena, paso por paso:
--
--   1. `quitar_precio` hace `DELETE FROM precios`.
--   2. El disparador genérico `fn_registrar_cambio` publica el delta con
--      `operacion = 'delete'` y **`payload = NULL`** — así está escrito desde la
--      0010, y para una entidad que se identifica por su `id` es correcto.
--   3. Pero el delta de `precio` lleva `entidad_id = producto_id`, no la llave del
--      renglón: un producto tiene un precio por lista y por presentación. Con el
--      payload en NULL, el teléfono no puede saber CUÁL de ellos se quitó.
--   4. Así que el aplicador no hace nada —`if (p == null) return true;`— y el
--      renglón viejo se queda en el SQLite del teléfono.
--
-- El resultado en la calle: el vendedor sigue ofreciendo una presentación que la
-- oficina retiró, al precio que tenía. La venta entra, y el servidor la marca con
-- `precio_desactualizado` — una marca que nadie entiende, porque el precio que el
-- teléfono usó ya no existe en ninguna lista.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- EL ARREGLO: EN UN DELETE, EL PAYLOAD DICE QUÉ SE BORRÓ
-- ─────────────────────────────────────────────────────────────────────────────
-- Un disparador propio para `precios` que, al borrar, emite los tres campos que
-- identifican el renglón. La operación sigue siendo `delete` —el teléfono tiene
-- que borrar, no actualizar— y el payload deja de estar vacío.
--
-- No se cambia el disparador genérico: para `producto` o `cliente`, que se
-- identifican por su `id`, el payload en NULL es correcto y mandar la fila
-- completa de algo que ya no existe solo engordaría el delta.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_registrar_cambio_precio() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
BEGIN
    INSERT INTO change_log (entidad, entidad_id, operacion, payload)
    VALUES (
        'precio',
        (v_registro ->> 'producto_id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
        -- En un DELETE viajan SOLO los tres campos que identifican el renglón.
        -- Mandar la fila entera diría «este precio vale 296.00» de algo que ya no
        -- existe, y un aplicador distraído podría insertarlo de vuelta.
        CASE
            WHEN TG_OP = 'DELETE' THEN jsonb_build_object(
                'lista_id',      v_registro -> 'lista_id',
                'producto_id',   v_registro -> 'producto_id',
                'unidad_codigo', v_registro -> 'unidad_codigo'
            )
            ELSE v_registro
        END
    );
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_registrar_cambio_precio() IS
    'Publica los precios al dispositivo. A diferencia del disparador genérico, en '
    'un DELETE manda los tres campos que identifican el renglón: el delta de '
    'precio se acota por producto_id, y sin ellos el teléfono no sabría cuál '
    'quitar — y seguiría ofreciendo una presentación retirada.';

DROP TRIGGER IF EXISTS trg_cambio_precio ON precios;
CREATE TRIGGER trg_cambio_precio
    AFTER INSERT OR UPDATE OR DELETE ON precios
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_precio();

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ NO SE PUSO
-- -----------------------------------------------------------------------------
-- **Editar el SKU y eliminar productos no necesitan migración.** El SKU ya viaja
-- en el payload del delta de `producto` y el teléfono ya lo sobrescribe; y el
-- aplicador ya trata un `delete` de producto como una BAJA («no se borra: un
-- producto retirado puede seguir apareciendo en ventas ya hechas que aún no
-- sincronizan»), que es exactamente lo que se necesita. Las dos cosas se hacen en
-- el panel, con sus validaciones y su auditoría.
