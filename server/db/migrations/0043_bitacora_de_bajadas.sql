-- =============================================================================
-- 0043 · La bitácora de lo que cada teléfono bajó
-- =============================================================================
-- Cada SUBIDA ya queda registrada (`sync_lotes`, con su desglose, y
-- `sync_operaciones`, una por documento). Las BAJADAS no: el pull solo movía el
-- cursor y la hora en `dispositivos`. Así no se podía contestar «¿el teléfono de
-- Juan ya recibió el ajuste que le hice a las diez?» sin preguntarle a Juan.
--
-- Se registra cada pull que ENTREGÓ algo, con cuántos cambios y de qué tipo. Un
-- pull vacío no deja renglón —los teléfonos preguntan cada pocos minutos y la
-- bitácora se llenaría de nada—, pero sí actualiza la hora de la última bajada
-- en `dispositivos`, que es lo que dice que el teléfono sigue vivo.
--
-- Se poda junto con el `change_log`: lo que ya no está en el libro de cambios
-- tampoco necesita su constancia de entrega.
-- =============================================================================

CREATE TABLE sync_bajadas (
    id              bigserial PRIMARY KEY,
    dispositivo_id  uuid NOT NULL REFERENCES dispositivos(id) ON DELETE CASCADE,
    usuario_id      uuid REFERENCES usuarios(id) ON DELETE SET NULL,
    cursor_desde    bigint NOT NULL,
    cursor_hasta    bigint NOT NULL,
    cambios         integer NOT NULL CHECK (cambios >= 0),
    -- {"cliente": 3, "ajuste_camion": 1}: qué bajó, para confirmar que llegó.
    entidades       jsonb NOT NULL DEFAULT '{}'::jsonb,
    hay_mas         boolean NOT NULL DEFAULT false,
    resincronizar   boolean NOT NULL DEFAULT false,
    ocurrido_en     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_sync_bajadas_fecha ON sync_bajadas (ocurrido_en DESC);
CREATE INDEX idx_sync_bajadas_dispositivo ON sync_bajadas (dispositivo_id, ocurrido_en DESC);

COMMENT ON TABLE sync_bajadas IS
    'Cada pull que entregó cambios a un teléfono. La subida tiene sync_lotes.';
