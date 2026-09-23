-- =============================================================================
-- 0010 · Poblado del change_log por trigger
-- =============================================================================
-- El change_log se llena en la BASE DE DATOS, no en la aplicación. Si
-- dependiera de que cada ruta de la API se acuerde de registrar el cambio, el
-- día que alguien corrija un precio con un UPDATE desde psql —y va a pasar—
-- los dispositivos nunca se enterarían y seguirían vendiendo al precio viejo.
--
-- Con trigger, no hay forma de escribir sin dejar rastro.
-- =============================================================================

-- TG_ARGV[0] = nombre de la entidad tal como la conoce el dispositivo
-- TG_ARGV[1] = columna que hace de entidad_id
-- TG_ARGV[2] = columna con la ruta, si la entidad está acotada por ruta
CREATE OR REPLACE FUNCTION fn_registrar_cambio() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_registro jsonb;
    v_entidad_id uuid;
    v_ruta uuid := NULL;
BEGIN
    v_registro := to_jsonb(COALESCE(NEW, OLD));

    -- La geografía generada se va: el dispositivo ya recibe lat y lng, y el
    -- hexadecimal de PostGIS solo engordaría cada delta.
    v_registro := v_registro - 'ubicacion';

    v_entidad_id := (v_registro ->> TG_ARGV[1])::uuid;

    IF TG_NARGS > 2 AND TG_ARGV[2] IS NOT NULL THEN
        v_ruta := (v_registro ->> TG_ARGV[2])::uuid;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
    VALUES (
        TG_ARGV[0],
        v_entidad_id,
        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
        v_ruta,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE v_registro END
    );

    RETURN NULL;   -- AFTER trigger: el valor de retorno se ignora
END;
$$;

COMMENT ON FUNCTION fn_registrar_cambio() IS
    'Alimenta change_log. El cursor del pull es el BIGSERIAL de esa tabla, '
    'nunca un timestamp: con relojes desincronizados y transacciones '
    'concurrentes, un cursor por fecha pierde registros en silencio.';

-- -----------------------------------------------------------------------------
-- Catálogo: llega a todos los dispositivos (ruta_id NULL)
-- -----------------------------------------------------------------------------
CREATE TRIGGER trg_cambio_producto
    AFTER INSERT OR UPDATE OR DELETE ON productos
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('producto', 'id');

CREATE TRIGGER trg_cambio_producto_unidad
    AFTER INSERT OR UPDATE OR DELETE ON producto_unidades
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('producto_unidad', 'producto_id');

CREATE TRIGGER trg_cambio_precio
    AFTER INSERT OR UPDATE OR DELETE ON precios
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('precio', 'producto_id');

CREATE TRIGGER trg_cambio_lista_precios
    AFTER INSERT OR UPDATE OR DELETE ON listas_precios
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('lista_precios', 'id');

CREATE TRIGGER trg_cambio_promocion
    AFTER INSERT OR UPDATE OR DELETE ON promociones
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('promocion', 'id');

-- -----------------------------------------------------------------------------
-- Clientes: acotados por ruta
-- -----------------------------------------------------------------------------
-- Un vendedor no debe recibir la cartera de las rutas ajenas. El acotamiento
-- va aquí y también en la consulta del pull: el dato no sale del servidor si
-- no le corresponde.
CREATE TRIGGER trg_cambio_cliente
    AFTER INSERT OR UPDATE OR DELETE ON clientes
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('cliente', 'id', 'ruta_id');

-- -----------------------------------------------------------------------------
-- Cargas: solo le importan a su vendedor
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_registrar_cambio_carga() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
BEGIN
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

CREATE TRIGGER trg_cambio_carga
    AFTER INSERT OR UPDATE OR DELETE ON cargas
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_carga();
