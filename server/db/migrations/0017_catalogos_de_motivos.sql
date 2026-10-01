-- =============================================================================
-- 0017 · Los catálogos de motivos, al teléfono
-- =============================================================================
-- `motivos_merma` y `motivos_no_drop` existen en el esquema local del dispositivo
-- desde el primer día y **nada los llenaba**. El vendedor habría abierto la
-- pantalla de merma con la lista de motivos vacía y no habría podido registrar
-- nada — y el motivo es de catálogo cerrado justamente para que no pueda escribir
-- texto libre.
--
-- Es el mismo defecto que la lista de precios tuvo hasta la migración 0013, por la
-- misma causa: los dos catálogos se siembran en la 0009, ANTES de que existieran
-- los disparadores de change_log de la 0010, así que nunca tuvieron renglón.
--
-- Se nota en el primer teléfono real, en la calle, cuando una caja se rompe.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- POR QUÉ `entidad_id` SALE DE UN md5
-- ─────────────────────────────────────────────────────────────────────────────
-- `change_log.entidad_id` es `uuid NOT NULL`, y estos catálogos se identifican por
-- un `codigo` de texto ('CADUCADO', 'CERRADO'). Un md5 del código da un UUID
-- estable y determinista: el mismo motivo produce siempre el mismo id, así que
-- republicarlo no genera una entidad nueva.
--
-- El `codigo` real viaja en el payload, que es lo que el dispositivo usa como
-- llave primaria. El `entidad_id` solo sirve para ordenar y deduplicar.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_registrar_cambio_catalogo_texto() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
BEGIN
    INSERT INTO change_log (entidad, entidad_id, operacion, payload)
    VALUES (
        TG_ARGV[0],
        md5(v_registro ->> 'codigo')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE v_registro END
    );
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_cambio_motivo_merma
    AFTER INSERT OR UPDATE OR DELETE ON motivos_merma
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_catalogo_texto('motivo_merma');

CREATE TRIGGER trg_cambio_motivo_no_drop
    AFTER INSERT OR UPDATE OR DELETE ON motivos_no_drop
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio_catalogo_texto('motivo_no_drop');

-- -----------------------------------------------------------------------------
-- El backfill: lo sembrado en la 0009 nunca tuvo renglón.
-- -----------------------------------------------------------------------------
-- Sin `ruta_id` ni `vendedor_id`: son catálogo global, le importan a todos los
-- equipos. Idempotente por el NOT EXISTS, para que correrla dos veces no duplique.
INSERT INTO change_log (entidad, entidad_id, operacion, payload)
SELECT 'motivo_merma', md5(m.codigo)::uuid, 'upsert', to_jsonb(m)
  FROM motivos_merma m
 WHERE NOT EXISTS (
       SELECT 1 FROM change_log c
        WHERE c.entidad = 'motivo_merma' AND c.entidad_id = md5(m.codigo)::uuid
 );

INSERT INTO change_log (entidad, entidad_id, operacion, payload)
SELECT 'motivo_no_drop', md5(m.codigo)::uuid, 'upsert', to_jsonb(m)
  FROM motivos_no_drop m
 WHERE NOT EXISTS (
       SELECT 1 FROM change_log c
        WHERE c.entidad = 'motivo_no_drop' AND c.entidad_id = md5(m.codigo)::uuid
 );
