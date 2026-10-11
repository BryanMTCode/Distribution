-- =============================================================================
-- 0054 · La visita reprogramada
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §103): «cuando los visites esos
-- días y el cliente no les va a comprar y les dice "ven mañana" o tal día, que
-- les aparezca la opción de reprogramar esa visita para el día que el cliente
-- les solicita, pero la configuración inicial de días sigue fija».
--
-- Dos piezas:
--
--   · `no_drops.reprogramada_para`: el día que el cliente pidió, junto a la
--     visita que no terminó en venta. Ahí nace porque es ahí donde se dice: «hoy
--     no, ven el jueves». Viaja en el mismo sobre del no-drop.
--   · `clientes.visita_reprogramada`: la última reprogramación, en el cliente,
--     para que viaje al teléfono dentro del delta de `cliente` que ya existe —sin
--     entidad nueva en la sincronización—, igual que el plan (0041). Así el
--     cliente sale en «Hoy» ese día aunque el teléfono se haya limpiado o lo
--     lleve otro vendedor. La mantiene un disparador.
--
-- El plan de días (`clientes_frecuencia`) NO se toca: reprogramar mueve una
-- visita, no los días del cliente.
-- =============================================================================

ALTER TABLE no_drops
    ADD COLUMN IF NOT EXISTS reprogramada_para date;

ALTER TABLE no_drops DROP CONSTRAINT IF EXISTS reprogramada_despues;
ALTER TABLE no_drops
    ADD CONSTRAINT reprogramada_despues
        CHECK (reprogramada_para IS NULL OR reprogramada_para > fecha_operativa);

COMMENT ON COLUMN no_drops.reprogramada_para IS
    'El día al que el cliente pidió que se volviera («ven mañana»). Esa visita '
    'cuenta en el plan de ese día; los días fijos del cliente no cambian.';

ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS visita_reprogramada date;

COMMENT ON COLUMN clientes.visita_reprogramada IS
    'La última visita reprogramada (no_drops.reprogramada_para), copiada aquí para '
    'que viaje al teléfono con el cliente. La mantiene trg_visita_reprogramada.';

CREATE OR REPLACE FUNCTION fn_visita_reprogramada() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE clientes
       SET visita_reprogramada = NEW.reprogramada_para,
           actualizado_en = now()
     WHERE id = NEW.cliente_id
       AND visita_reprogramada IS DISTINCT FROM NEW.reprogramada_para;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_visita_reprogramada ON no_drops;
CREATE TRIGGER trg_visita_reprogramada
    AFTER INSERT ON no_drops
    FOR EACH ROW
    WHEN (NEW.reprogramada_para IS NOT NULL)
    EXECUTE FUNCTION fn_visita_reprogramada();
