-- =============================================================================
-- 0063 · El corte del vendedor se cierra solo
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §133): «el corte será
-- automático, no tendrá que ser validado por el supervisor; solo lo hará el
-- vendedor: le aparecerá la pantalla para confirmar y entonces imprimirá el
-- ticket o lo podrá enviar por WhatsApp».
--
-- El corte que manda el teléfono ya no espera a la oficina: el worker lo cierra
-- en cuanto llega y el teléfono terminó de subir (`cerrar_corte_del_vendedor`).
-- Lo que lo puede detener son las mismas reglas de siempre —el teléfono con
-- operaciones sin subir, algo en cuarentena—. Cuando pasa, el corte se queda por
-- cerrar y aquí queda escrito por qué, para que la oficina lo vea y lo cierre a
-- mano si hace falta. Al cerrarse, se borra.
-- =============================================================================

ALTER TABLE cortes_vendedor ADD COLUMN IF NOT EXISTS no_se_cerro_solo text;

COMMENT ON COLUMN cortes_vendedor.no_se_cerro_solo IS
    'Por qué el corte no se cerró solo al llegar (ADR 0002 §133). Nulo si se '
    'cerró, o si todavía no se intenta.';
