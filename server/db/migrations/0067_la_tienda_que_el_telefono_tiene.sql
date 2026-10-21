-- =============================================================================
-- 0067 · La tienda que el teléfono tiene, aunque el alcance no la deje ver
-- =============================================================================
-- «Hice una venta a un cliente que no me tocaba hoy y lo quise sincronizar y no
-- me dejó, luego la cancelé y ahora para sincronizarlas las dos me aparecen con
-- error» (octubre 2026, ADR 0002 §165).
--
-- Con el rol de producción (`dsd_api`, con RLS) el vendedor solo VE las tiendas
-- de sus rutas (`clientes_alcance`, 0022). Pero el teléfono puede tener tiendas
-- que el alcance no le enseña:
--
--   · una tienda SIN ruta: su delta sale con `ruta_id` NULL, y eso quiere decir
--     «a todos los teléfonos» (0007). Le llega a todos y no la ve ninguno;
--   · una tienda que la oficina pasó a otra ruta, o una ruta que se le quitó al
--     vendedor, mientras el teléfono vendía sin señal: la tenía cuando vendió.
--
-- El manejador de la venta buscaba la tienda con un SELECT común, RLS la
-- escondía, y la venta se iba a cuarentena diciendo «la venta referencia un
-- cliente que no existe». Detrás, su cancelación: «la venta a cancelar no
-- existe». Las dos con error, y la tienda existiendo. Con las pruebas corriendo
-- como dueño de las tablas —que salta RLS— no se veía.
--
-- Esta función le da al manejador SOLO lo que necesita para registrar el
-- documento —la ruta, la lista de precios y la ubicación de la tienda, para la
-- geocerca—, por su id. No lista tiendas ni enseña nombres ni domicilios: hace
-- falta el UUID, y el UUID solo lo tiene quien ya recibió la tienda. La regla de
-- la ruta sigue en el manejador: la tienda de otra ruta entra marcada (§0.1).
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_cliente_del_telefono(p_cliente uuid)
RETURNS TABLE (ruta_id uuid, lista_precios_id uuid, lat numeric, lng numeric)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT c.ruta_id, c.lista_precios_id, c.lat, c.lng
      FROM clientes c
     WHERE c.id = p_cliente
$$;

COMMENT ON FUNCTION fn_cliente_del_telefono(uuid) IS
    'La ruta, la lista y la ubicación de una tienda, por su id, para registrar lo '
    'que manda el teléfono aunque el alcance (RLS) no se la enseñe al vendedor: '
    'una tienda sin ruta, o una que cambió de ruta mientras vendía sin señal.';
