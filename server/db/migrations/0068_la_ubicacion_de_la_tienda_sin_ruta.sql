-- =============================================================================
-- 0068 · La ubicación de una tienda sin ruta, corregida desde el teléfono
-- =============================================================================
-- El mismo hueco que la 0067 cerró para la venta, en otra operación (ADR 0002
-- §172). Con el rol de producción (`dsd_api`, con RLS) el vendedor solo VE y
-- solo CAMBIA las tiendas de sus rutas (`clientes_alcance`, `clientes_cambio`,
-- 0022). Una tienda SIN ruta le llega a todos los teléfonos (0007), el vendedor
-- le vende —eso ya entra (0067)— y le corrige la ubicación desde su ficha. Esa
-- corrección buscaba la tienda con un SELECT común, RLS la escondía, y se iba a
-- cuarentena: «ese cliente no existe».
--
-- Esta función guarda la ubicación SOLO si la tienda no tiene ruta, y devuelve
-- la que tenía para dejarla en la auditoría. La de otra ruta no pasa por aquí:
-- esa la sigue moviendo la oficina (§165).
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_ubicar_tienda_sin_ruta(
    p_cliente   uuid,
    p_lat       numeric,
    p_lng       numeric,
    p_origen    text,
    p_precision numeric,
    p_momento   timestamptz
)
RETURNS TABLE (lat_antes numeric, lng_antes numeric, origen_antes text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    antes record;
BEGIN
    SELECT c.lat, c.lng, c.ubicacion_origen
      INTO antes
      FROM clientes c
     WHERE c.id = p_cliente AND c.ruta_id IS NULL
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    UPDATE clientes
       SET lat = p_lat, lng = p_lng, ubicacion_origen = p_origen,
           ubicacion_precision_m = p_precision, ubicacion_capturada_en = p_momento,
           actualizado_en = now()
     WHERE id = p_cliente;

    lat_antes := antes.lat;
    lng_antes := antes.lng;
    origen_antes := antes.ubicacion_origen;
    RETURN NEXT;
END
$$;

COMMENT ON FUNCTION fn_ubicar_tienda_sin_ruta(uuid, numeric, numeric, text, numeric, timestamptz) IS
    'La ubicación que el vendedor corrigió en una tienda SIN ruta, que RLS no le '
    'deja cambiar. Devuelve la anterior para la auditoría (ADR 0002 §172).';
