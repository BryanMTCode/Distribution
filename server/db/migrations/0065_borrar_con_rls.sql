-- =============================================================================
-- 0065 · Lo que el panel borra, que con el rol de producción no se borraba
-- =============================================================================
-- «VEND00 no se puede borrar: tiene registros en change_log» (octubre 2026,
-- ADR 0002 §138). Tres borrados del panel chocaban con la seguridad por renglón
-- (0022) solo en producción: ahí la API habla como `dsd_api`, sin BYPASSRLS, y
-- una tabla con RLS y sin política de DELETE no deja borrar NADA —sin error: el
-- DELETE simplemente no ve renglones—. Las pruebas usan el rol dueño, que salta
-- RLS, y por eso pasaban.
--
-- -----------------------------------------------------------------------------
-- 1. Los avisos del usuario que se elimina (change_log)
-- -----------------------------------------------------------------------------
-- Al eliminar un usuario, el panel borra los avisos que esperaban a su teléfono
-- (`change_log` con su `vendedor_id`): ya no hay teléfono que los lea, y su llave
-- foránea detendría el borrado. Lo hacía con un DELETE directo. Pero
-- `change_log` tiene RLS (0022) con una sola política, de LECTURA: con el rol de
-- producción (`dsd_api`, sin BYPASSRLS) un DELETE no ve ningún renglón y no
-- borra nada, sin error. Bastaba con que el vendedor tuviera camión —su
-- disparador de identidad le deja un aviso— para que no se pudiera eliminar.
-- Con el rol dueño, el de las pruebas, sí borraba.
--
-- No se agrega una política de DELETE: abriría `change_log` a cualquier DELETE
-- de la API. Como con los disparadores (0029), el borrado pasa por una función
-- `SECURITY DEFINER` que hace solo eso, y solo con alcance de oficina.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_olvidar_avisos_del_usuario(p_usuario uuid) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_borrados integer;
BEGIN
    IF NOT dsd_ve_todo() THEN
        RAISE EXCEPTION 'solo la oficina puede borrar los avisos de un usuario'
            USING ERRCODE = '42501';
    END IF;
    DELETE FROM change_log WHERE vendedor_id = p_usuario;
    GET DIAGNOSTICS v_borrados = ROW_COUNT;
    RETURN v_borrados;
END;
$$;

COMMENT ON FUNCTION fn_olvidar_avisos_del_usuario(uuid) IS
    'Borra los avisos de change_log de un usuario que se elimina. SECURITY DEFINER: '
    'change_log tiene RLS solo de lectura, y con el rol de la API un DELETE no borra '
    'nada. Solo con alcance de oficina (dsd_ve_todo).';

-- -----------------------------------------------------------------------------
-- 2. La tienda sin historia que se elimina (clientes)
-- -----------------------------------------------------------------------------
-- La 0022 decía «nadie borra clientes»; después el panel aprendió a eliminar la
-- tienda que no tiene ventas ni nada (las que sí, se dan de baja). Con el rol de
-- producción decía «se eliminó» y la tienda se quedaba. Solo la oficina.
DROP POLICY IF EXISTS clientes_baja_de_oficina ON clientes;
CREATE POLICY clientes_baja_de_oficina ON clientes
    FOR DELETE
    USING (dsd_ve_todo());

-- -----------------------------------------------------------------------------
-- 3. La visita sin venta que se elimina en Operaciones (no_drops, §119)
-- -----------------------------------------------------------------------------
-- No tiene estado: anularla es borrarla, y lo que la describía queda en la
-- anulación. Con el rol de producción se quedaba. Solo la oficina.
DROP POLICY IF EXISTS no_drops_baja_de_oficina ON no_drops;
CREATE POLICY no_drops_baja_de_oficina ON no_drops
    FOR DELETE
    USING (dsd_ve_todo());
