-- =============================================================================
-- 0011 · Delta de cartera
-- =============================================================================
-- El delta de `clientes` lleva la fila de esa tabla, y el saldo NO está ahí:
-- vive agregado en `v_cartera_cliente`, sobre `cuentas_por_cobrar`. Sin este
-- trigger, el dispositivo recibiría el límite de crédito pero nunca el saldo, y
-- el cálculo de crédito local trabajaría con cero: le diría al vendedor que
-- tiene toda su línea disponible a un cliente que debe hasta el cuello.
--
-- Cada movimiento de cartera emite un delta con el saldo recalculado de ese
-- cliente. Es la pieza que cierra el bloqueo automático del ADR 0002.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_registrar_cambio_cartera() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_cliente uuid := COALESCE(NEW.cliente_id, OLD.cliente_id);
    v_fila jsonb;
BEGIN
    SELECT to_jsonb(v) - 'nombre_comercial' - 'codigo'
      INTO v_fila
      FROM v_cartera_cliente v
     WHERE v.cliente_id = v_cliente;

    -- Un cliente dado de baja desaparece de la vista: no hay cartera que
    -- anunciar, y el delta de `clientes` ya informó la baja.
    IF v_fila IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
    VALUES (
        'cartera',
        v_cliente,
        'upsert',
        (v_fila ->> 'ruta_id')::uuid,
        v_fila
    );
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_registrar_cambio_cartera() IS
    'Emite el saldo agregado del cliente cuando cambia su cartera. Sin esto el '
    'dispositivo calcularía el crédito con saldo cero. Ver ADR 0002.';

CREATE TRIGGER trg_cambio_cartera
    AFTER INSERT OR UPDATE OR DELETE ON cuentas_por_cobrar
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_cartera();

-- Cambiar el límite o el bloqueo también altera el crédito disponible, y esa
-- edición ocurre en `clientes`, no en la cartera. El delta de `clientes` ya
-- viaja, pero sin el saldo: se emite además el de cartera para que el
-- dispositivo recomponga los dos números juntos.
CREATE OR REPLACE FUNCTION fn_cartera_por_condiciones() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_fila jsonb;
BEGIN
    IF NEW.limite_credito IS NOT DISTINCT FROM OLD.limite_credito
       AND NEW.permite_credito IS NOT DISTINCT FROM OLD.permite_credito
       AND NEW.bloqueado IS NOT DISTINCT FROM OLD.bloqueado THEN
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

CREATE TRIGGER trg_cartera_por_condiciones
    AFTER UPDATE ON clientes
    FOR EACH ROW EXECUTE FUNCTION fn_cartera_por_condiciones();
