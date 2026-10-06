-- =============================================================================
-- 0042 · Cuando una ruta cambia de manos, sus clientes viajan con ella
-- =============================================================================
-- CORRIGE UN HUECO QUE ENCONTRÓ LA AUDITORÍA PANEL → TELÉFONO.
--
-- El panel cambia el titular de una ruta escribiendo `usuarios_rutas`: le quita
-- la ruta al vendedor anterior y se la da al nuevo. El *scope guard* empieza a
-- filtrar con el alcance nuevo, pero el `change_log` no se enteraba de nada:
--
--   · El teléfono del vendedor NUEVO ya tenía su cursor más allá de los deltas
--     de esos clientes —se publicaron cuando se dieron de alta, quizá hace un
--     año— y el pull solo entrega lo posterior al cursor. Resultado: la ruta le
--     llegaba VACÍA. Solo veía a un cliente cuando alguien lo editaba.
--   · El teléfono del vendedor ANTERIOR se quedaba con todos los clientes. Podía
--     venderles, y cada venta caía en cuarentena porque la ruta ya no es suya.
--
-- Lo mismo pasaba al crear una ruta con titular sobre clientes que ya existían,
-- o al dar de baja una ruta.
--
-- La corrección vive en la base, junto a los demás disparadores del
-- `change_log`, para que no dependa de que cada pantalla del panel se acuerde:
--
--   · ALTA en `usuarios_rutas` → se republican los clientes de la ruta y su
--     cartera, dirigidos SOLO a ese vendedor (`vendedor_id`), y con `ruta_id`
--     NULL a propósito: el delta tiene que llegarle aunque el pull todavía no
--     sepa que la ruta es suya. Los demás teléfonos de la ruta ya los tienen.
--   · BAJA en `usuarios_rutas` → un `delete` de cada cliente, también dirigido
--     solo a ese vendedor. El teléfono no borra: deja al cliente inactivo, que
--     es lo que hace con cualquier `delete` de cliente.
--
-- El payload es el MISMO que publica `fn_registrar_cambio_cliente` (la fila sin
-- la geografía) y el de la cartera sale de la misma vista: el teléfono no tiene
-- que distinguir un delta republicado de uno ordinario.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_alcance_de_ruta() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
        SELECT 'cliente', c.id, 'upsert', NEW.usuario_id, to_jsonb(c) - 'ubicacion'
          FROM clientes c
         WHERE c.ruta_id = NEW.ruta_id
         ORDER BY c.secuencia NULLS LAST, c.id;

        -- La cartera después de los clientes: el teléfono aplica en orden, y así
        -- el saldo nunca llega antes que el cliente al que pertenece.
        INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
        SELECT 'cartera', v.cliente_id, 'upsert', NEW.usuario_id,
               to_jsonb(v) - 'nombre_comercial' - 'codigo'
          FROM v_cartera_cliente v
         WHERE v.ruta_id = NEW.ruta_id
         ORDER BY v.cliente_id;
        RETURN NULL;
    END IF;

    -- Cuando el usuario mismo se está borrando (ON DELETE CASCADE), ya no hay a
    -- quién avisarle, y la llave de `change_log.vendedor_id` rechazaría el aviso.
    IF NOT EXISTS (SELECT 1 FROM usuarios WHERE id = OLD.usuario_id) THEN
        RETURN NULL;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, vendedor_id, payload)
    SELECT 'cliente', c.id, 'delete', OLD.usuario_id, NULL
      FROM clientes c
     WHERE c.ruta_id = OLD.ruta_id
     ORDER BY c.id;
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_alcance_de_ruta() IS
    'Al dar o quitar una ruta a un vendedor, le manda (o le retira) sus clientes y '
    'su cartera. Sin esto, el teléfono del titular nuevo recibía la ruta vacía.';

CREATE TRIGGER trg_alcance_de_ruta
    AFTER INSERT OR DELETE ON usuarios_rutas
    FOR EACH ROW EXECUTE FUNCTION fn_alcance_de_ruta();

-- =============================================================================
-- La cartera viaja con el cliente, y su disparador corre como su dueño
-- =============================================================================
-- DOS HUECOS MÁS DE LA MISMA AUDITORÍA, en `fn_cartera_por_condiciones` (0011):
--
-- 1. **Un cliente con deuda que cambia de ruta llegaba al teléfono nuevo con saldo
--    cero.** El delta del cliente sí viaja (`fn_registrar_cambio_cliente` manda la
--    baja a la ruta vieja y el alta a la nueva), pero el saldo vive en el delta de
--    `cartera`, y ese solo se publicaba al cambiar el límite, el permiso de
--    crédito o el bloqueo. El teléfono nuevo veía al cliente sin deuda y con toda
--    su línea libre: le vendía a crédito por encima de su límite. Lo mismo al
--    reactivar a un cliente dado de baja.
--
--    Ahora también se publica cuando cambia `ruta_id` o `estatus`. Los disparadores
--    AFTER corren en orden alfabético —`trg_cambio_cliente` antes que
--    `trg_cartera_por_condiciones`—, así que el cliente llega antes que su saldo.
--
-- 2. **No era `SECURITY DEFINER`.** La 0029 corrigió las funciones que escribían
--    en `change_log` y esta no estaba en su lista. En producción, con RLS de
--    verdad, subirle el límite de crédito a un cliente desde el panel terminaba en
--    500. `test_rls.py` tiene ahora una guardia que lee el catálogo y revisa TODAS.

CREATE OR REPLACE FUNCTION fn_cartera_por_condiciones() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_fila jsonb;
BEGIN
    IF NEW.limite_credito IS NOT DISTINCT FROM OLD.limite_credito
       AND NEW.permite_credito IS NOT DISTINCT FROM OLD.permite_credito
       AND NEW.bloqueado IS NOT DISTINCT FROM OLD.bloqueado
       AND NEW.ruta_id IS NOT DISTINCT FROM OLD.ruta_id
       AND NEW.estatus IS NOT DISTINCT FROM OLD.estatus THEN
        RETURN NULL;
    END IF;

    SELECT to_jsonb(v) - 'nombre_comercial' - 'codigo'
      INTO v_fila
      FROM v_cartera_cliente v
     WHERE v.cliente_id = NEW.id;

    IF v_fila IS NOT NULL THEN
        INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
        VALUES ('cartera', NEW.id, 'upsert', NEW.ruta_id, v_fila);
    END IF;
    RETURN NULL;
END;
$$;
