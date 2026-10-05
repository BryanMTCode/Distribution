-- =============================================================================
-- 0034 · Blindaje de la sincronización: el cliente que cambia de ruta
-- =============================================================================
-- Hallazgo 3 de la auditoría de octubre de 2026.
--
-- El `change_log` acota cada delta de cliente por `ruta_id`, y el pull entrega
-- solo lo de las rutas del vendedor. Funciona para todo menos para un caso, y es
-- un caso rutinario: **reasignar un cliente a otra ruta**.
--
--   1. La oficina cambia `clientes.ruta_id` de A a B.
--   2. El disparador genérico publica el delta con el registro NUEVO, así que el
--      delta va acotado a la ruta **B**.
--   3. El teléfono de la ruta A no lo recibe nunca. Para él, ese cliente sigue
--      siendo suyo: lo tiene en su lista, lo visita, le vende.
--
-- Y la venta que haga entra sin protestar —el cliente existe en el servidor—, así
-- que no hay ninguna señal de que dos vendedores están cubriendo al mismo cliente
-- hasta que alguien compara las dos rutas a mano.
--
-- El mismo hueco, con otra cara: dar un cliente de baja (`estatus = 'inactivo'` o
-- `'baja'`) sí viajaba, pero el teléfono no guardaba `estatus` ni filtraba por él,
-- así que seguía mandando al vendedor a la puerta de un cliente que la empresa ya
-- había dado por perdido. Eso se arregló del lado del teléfono.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- EL AVISO A LA RUTA QUE PIERDE EL CLIENTE
-- ─────────────────────────────────────────────────────────────────────────────
-- Un disparador propio para `clientes` que, cuando la ruta cambia, publica DOS
-- deltas: el `upsert` normal para la ruta nueva y un `delete` para la vieja.
--
-- `delete` y no un payload con la ruta nueva, porque lo que el teléfono viejo
-- tiene que hacer es exactamente lo que hace con un borrado: sacar al cliente de
-- su lista. Y lo hace **dando de baja, no borrando** —ver el aplicador—: sus
-- ventas y cobros sin sincronizar siguen en pie y se suben igual.
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

    -- El aviso a la ruta que lo pierde, ANTES del alta en la nueva: si las dos
    -- llegan en la misma tanda, el teléfono que lo pierde aplica su baja y el que
    -- lo gana aplica su alta, cada uno con el delta que le toca.
    IF TG_OP = 'UPDATE'
       AND OLD.ruta_id IS DISTINCT FROM NEW.ruta_id
       AND OLD.ruta_id IS NOT NULL THEN
        INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, payload)
        VALUES ('cliente', OLD.id, 'delete', OLD.ruta_id, NULL);
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
    'Publica los clientes al dispositivo. A diferencia del disparador genérico, '
    'cuando la ruta cambia avisa también a la ruta que PIERDE al cliente: si no, '
    'ese teléfono lo visitaría para siempre.';

DROP TRIGGER IF EXISTS trg_cambio_cliente ON clientes;
CREATE TRIGGER trg_cambio_cliente
    AFTER INSERT OR UPDATE OR DELETE ON clientes
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_cliente();

-- -----------------------------------------------------------------------------
-- El piso de retención del change_log
-- -----------------------------------------------------------------------------
-- Hallazgo 4. La migración 0007 dice «un job lo poda conservando lo necesario
-- para el dispositivo más atrasado» y creó la vista para calcularlo. **Ese job no
-- existe**, así que hoy el `change_log` crece sin límite — una molestia de disco,
-- no un error.
--
-- Lo que sí es un error es lo que pasaría el día que alguien lo escriba: un
-- dispositivo cuyo cursor quede por debajo de lo podado recibiría los deltas
-- siguientes y **nunca sabría que le faltan los de en medio**. Se quedaría con un
-- catálogo incompleto, silenciosamente, para siempre.
--
-- Así que primero la defensa y después la poda: aquí se guarda cuál es el cursor
-- más bajo que todavía se conserva, y el pull lo compara contra el cursor que
-- traiga el dispositivo. Mientras nadie pode, el piso se queda en 0 y no cambia
-- nada; el día que se pode, el dispositivo atrasado recibe una señal de
-- «resincroniza desde cero» en vez de un hueco.
CREATE TABLE IF NOT EXISTS sync_retencion (
    id              boolean PRIMARY KEY DEFAULT true CHECK (id),
    piso_cursor     bigint NOT NULL DEFAULT 0,
    podado_en       timestamptz,
    CONSTRAINT una_sola_fila CHECK (id)
);

INSERT INTO sync_retencion (id, piso_cursor) VALUES (true, 0)
ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE sync_retencion IS
    'El cursor más bajo que el change_log todavía conserva. El pull lo compara '
    'contra el cursor del dispositivo para no entregarle un tramo con huecos. '
    'Una sola fila, garantizada por el CHECK.';
