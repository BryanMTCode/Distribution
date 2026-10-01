-- =============================================================================
-- 0016 · El consecutivo del folio de liquidación
-- =============================================================================
-- Mismo razonamiento que el código de cliente (0014) y el folio de carga (0015):
-- una secuencia, no `max(folio)+1`. Al final del día llegan varios vendedores a la
-- vez y dos personas pueden estar cerrando dos camiones al mismo tiempo; con
-- `max()+1` las dos obtienen el mismo número y una ve un error de UNIQUE que no
-- significa nada para ella.
--
-- Deja huecos cuando una transacción se deshace, y eso está bien: el folio
-- identifica una liquidación, no las cuenta.
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS seq_folio_liquidacion AS bigint START WITH 1;

-- El tercer argumento en `false` significa "el siguiente nextval devuelve
-- exactamente este número": sin él la secuencia se saltaría el LQ-000001.
SELECT setval(
    'seq_folio_liquidacion',
    (SELECT COALESCE(MAX(substring(folio FROM 4)::bigint), 0) + 1
       FROM liquidaciones
      WHERE folio ~ '^LQ-[0-9]+$'),
    false
);

COMMENT ON SEQUENCE seq_folio_liquidacion IS
    'Consecutivo del folio de liquidacion. Se consume al ABRIR el cierre del dia.';
