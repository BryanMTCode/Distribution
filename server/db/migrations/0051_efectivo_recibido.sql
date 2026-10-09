-- =============================================================================
-- 0051 · El efectivo que el gerente recibe al cerrar el corte
-- =============================================================================
-- Retroalimentación de la dirección (octubre 2026, ADR 0002 §87). El corte del
-- vendedor se cerraba con el efectivo que el vendedor DECÍA que entregaba: si
-- declaraba $2,250 y entregaba $2,000, el sistema no se enteraba y nadie le
-- cobraba la diferencia.
--
-- Ahora el gerente cuenta lo que recibe y lo escribe al cerrar. El arqueo y lo
-- que se carga a la cuenta del vendedor salen de ESE número; lo declarado se
-- queda al lado para que se vea cuándo no coincidieron.
--
-- Nulo mientras el corte no se cierra, y en los que se cerraron antes de esto.
-- =============================================================================

ALTER TABLE cortes_vendedor ADD COLUMN efectivo_recibido numeric(14,2)
    CHECK (efectivo_recibido >= 0);

COMMENT ON COLUMN cortes_vendedor.efectivo_recibido IS
    'Lo que el gerente contó al cerrar el corte. El arqueo sale de aquí; '
    '`efectivo_declarado` es lo que dijo el vendedor.';
