-- =============================================================================
-- 0041 · El plan de visita: qué clientes tocan cada día
-- =============================================================================
-- `clientes_frecuencia` existe desde la migración 0003 y ningún código la usaba.
-- Sin plan, «visita perdida» solo podía contar las visitas que dejaron un papel
-- (una venta o un no-drop): el cliente al que nadie fue no dejaba rastro, y un
-- vendedor que se saltaba ocho tiendas de treinta salía con efectividad perfecta.
--
-- Con plan, la oficina dice qué días toca cada cliente; el teléfono le muestra al
-- vendedor «hoy te tocan estos», y Efectividad cuenta lo que tocaba y nadie
-- visitó.
--
-- Dos piezas:
--
--   · `clientes_frecuencia.desde`: el plan cuenta a partir del día en que se
--     capturó. Sin esto, capturar el plan de una ruta hoy convertiría todos los
--     días anteriores en visitas «perdidas» que nadie podía haber hecho.
--   · `clientes.plan_visita`: el plan del cliente en una columna, para que viaje
--     al teléfono dentro del delta de `cliente` que ya existe —sin entidad nueva
--     en la sincronización—. Lo mantiene un disparador: la fuente de verdad sigue
--     siendo `clientes_frecuencia`.
-- =============================================================================

ALTER TABLE clientes_frecuencia
    ADD COLUMN desde date NOT NULL DEFAULT CURRENT_DATE;

COMMENT ON COLUMN clientes_frecuencia.desde IS
    'Desde qué día cuenta esta visita en el plan. Lo anterior no se le reclama a nadie.';

ALTER TABLE clientes
    ADD COLUMN plan_visita jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN clientes.plan_visita IS
    'Copia de clientes_frecuencia para el teléfono: [{"dia":0-6,"semana":1-4|null}]. '
    'dia 0 = domingo, como extract(dow). La mantiene trg_plan_visita_*.';

-- -----------------------------------------------------------------------------
-- El disparador que copia el plan al cliente
-- -----------------------------------------------------------------------------
-- A nivel de SENTENCIA, con tabla de transición: planear una ruta de cincuenta
-- clientes escribe ~150 renglones de frecuencia, y a nivel de renglón cada uno
-- publicaría un delta del cliente. Así cada cliente afectado se actualiza UNA vez
-- por sentencia, y solo si su plan de verdad cambió.
CREATE OR REPLACE FUNCTION fn_plan_visita_al_cliente() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE clientes c
       SET plan_visita = p.plan,
           actualizado_en = now()
      FROM (
            SELECT a.cliente_id,
                   COALESCE(
                     (SELECT jsonb_agg(jsonb_build_object('dia', f.dia_semana,
                                                          'semana', f.semana_del_mes)
                                       ORDER BY f.dia_semana, f.semana_del_mes NULLS FIRST)
                        FROM clientes_frecuencia f
                       WHERE f.cliente_id = a.cliente_id),
                     '[]'::jsonb) AS plan
              FROM (SELECT DISTINCT cliente_id FROM afectados) a
           ) p
     WHERE c.id = p.cliente_id
       AND c.plan_visita IS DISTINCT FROM p.plan;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_plan_visita_alta
    AFTER INSERT ON clientes_frecuencia
    REFERENCING NEW TABLE AS afectados
    FOR EACH STATEMENT EXECUTE FUNCTION fn_plan_visita_al_cliente();

CREATE TRIGGER trg_plan_visita_cambio
    AFTER UPDATE ON clientes_frecuencia
    REFERENCING NEW TABLE AS afectados
    FOR EACH STATEMENT EXECUTE FUNCTION fn_plan_visita_al_cliente();

CREATE TRIGGER trg_plan_visita_baja
    AFTER DELETE ON clientes_frecuencia
    REFERENCING OLD TABLE AS afectados
    FOR EACH STATEMENT EXECUTE FUNCTION fn_plan_visita_al_cliente();

-- -----------------------------------------------------------------------------
-- ¿Toca visitar a este cliente este día?
-- -----------------------------------------------------------------------------
-- La regla vive UNA vez en la base y su espejo en Dart (`plan_visita.dart`), con
-- las mismas pruebas de borde. La semana del mes es (día − 1) / 7 + 1: del 1 al 7
-- es la semana 1, del 29 en adelante la 5, que ningún plan «solo semana N» pide.
CREATE OR REPLACE FUNCTION toca_visita(dia date, dia_semana smallint, semana smallint)
RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT extract(dow FROM dia)::smallint = dia_semana
       AND (semana IS NULL OR semana = ((extract(day FROM dia)::int - 1) / 7 + 1));
$$;
