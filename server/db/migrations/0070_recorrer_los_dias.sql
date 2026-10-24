-- =============================================================================
-- 0070 · El plan de visita tiene fin: recorrer los días sin tocar lo trabajado
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §175): «quiero cambiar las
-- rutas que tienen asignadas, recorrerlas un día para atrás: lo que iban a
-- visitar el martes, que lo visiten el lunes; lo del miércoles, el martes, y así.
-- Que todo lo que han trabajado estos días siga como tal; solo que a partir del
-- lunes trabajen con las rutas actualizadas.»
--
-- El plan solo tenía `desde`. Cambiar un día era borrar el renglón y poner otro,
-- y con eso Efectividad medía los días ya trabajados contra el plan NUEVO: el
-- martes pasado, las tiendas del martes ya no «tocaban» y las del miércoles sí.
-- Lo trabajado se reescribía solo.
--
-- Ahora cada renglón dice también `hasta`: el plan que se recorre queda vigente
-- hasta el domingo, y el nuevo empieza el lunes. Quien mira un día —Efectividad,
-- la venta «fuera de su día», el mapa de la ruta— usa el plan que valía ese día.
-- Quien mira «el plan» —el teléfono, la lista de clientes, la pantalla del plan—
-- usa el vigente, el que no tiene `hasta`.
-- =============================================================================

ALTER TABLE clientes_frecuencia
    ADD COLUMN IF NOT EXISTS hasta date;

COMMENT ON COLUMN clientes_frecuencia.hasta IS
    'Hasta qué día cuenta esta visita en el plan; nulo, sigue vigente. Lo pone el '
    'comando recorrer-dias (ADR 0002 §175): el plan de antes se sigue midiendo '
    'con sus días.';

-- Uno que nunca llegó a contar (se cerró el día antes de empezar) es válido; uno
-- que termina antes de eso, no.
ALTER TABLE clientes_frecuencia
    ADD CONSTRAINT ck_frecuencia_hasta CHECK (hasta IS NULL OR hasta >= desde - 1);

-- La misma tienda puede tener el martes cerrado y el martes nuevo: único solo
-- entre los vigentes.
DROP INDEX IF EXISTS uq_cliente_frecuencia;
CREATE UNIQUE INDEX uq_cliente_frecuencia
    ON clientes_frecuencia (cliente_id, dia_semana, COALESCE(semana_del_mes::integer, 0))
    WHERE hasta IS NULL;

-- -----------------------------------------------------------------------------
-- ¿Vale este renglón del plan ese día?
-- -----------------------------------------------------------------------------
-- Hasta su `hasta`. Y desde su `desde` solo si la tienda tenía un plan cerrado
-- que cubra ese día: así el recorrido no se aplica hacia atrás. Sin plan cerrado,
-- `desde` no cuenta aquí —como hasta hoy en la venta «fuera de su día» (0067)—:
-- el plan que se acaba de capturar es el plan que la tienda tiene.
CREATE OR REPLACE FUNCTION frecuencia_vigente(
    p_cliente uuid, p_desde date, p_hasta date, p_dia date
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT (p_hasta IS NULL OR p_dia <= p_hasta)
       AND (p_desde <= p_dia
            OR NOT EXISTS (SELECT 1 FROM clientes_frecuencia g
                            WHERE g.cliente_id = p_cliente
                              AND g.hasta IS NOT NULL
                              AND g.hasta >= p_dia))
$$;

COMMENT ON FUNCTION frecuencia_vigente(uuid, date, date, date) IS
    'Si un renglón del plan de visita vale ese día: hasta su fin, y desde su '
    'inicio cuando reemplazó a otro (ADR 0002 §175).';

-- -----------------------------------------------------------------------------
-- Al teléfono, el plan vigente
-- -----------------------------------------------------------------------------
-- El teléfono no sabe de fechas en el plan: recibe el vigente en cuanto se
-- recorre, y por eso el recorrido se hace al terminar el día.
--
-- `dsd.plan_en_pausa` lo prende el recorrido mientras cierra el plan viejo, y lo
-- apaga antes de escribir el nuevo: así a cada tienda le llega UN cambio, el del
-- plan nuevo, y no antes uno con el plan vacío.
CREATE OR REPLACE FUNCTION fn_plan_visita_al_cliente() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF COALESCE(current_setting('dsd.plan_en_pausa', true), '') = 'si' THEN
        RETURN NULL;
    END IF;
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
                       WHERE f.cliente_id = a.cliente_id AND f.hasta IS NULL),
                     '[]'::jsonb) AS plan
              FROM (SELECT DISTINCT cliente_id FROM afectados) a
           ) p
     WHERE c.id = p.cliente_id
       AND c.plan_visita IS DISTINCT FROM p.plan;
    RETURN NULL;
END;
$$;

-- -----------------------------------------------------------------------------
-- La venta «fuera de su día» (0067), con el plan de ese día
-- -----------------------------------------------------------------------------
-- La venta del sábado que sube el domingo, ya recorrido el plan, se mide contra
-- el sábado de antes, no contra el nuevo.
CREATE OR REPLACE FUNCTION fn_le_toca_visita(p_cliente uuid, p_dia date)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT CASE
             WHEN EXISTS (SELECT 1 FROM no_drops n
                           WHERE n.cliente_id = p_cliente AND n.reprogramada_para = p_dia)
               THEN true
             WHEN NOT EXISTS (SELECT 1 FROM clientes_frecuencia f
                               WHERE f.cliente_id = p_cliente
                                 AND frecuencia_vigente(f.cliente_id, f.desde, f.hasta, p_dia))
               THEN NULL
             ELSE EXISTS (SELECT 1 FROM clientes_frecuencia f
                           WHERE f.cliente_id = p_cliente
                             AND frecuencia_vigente(f.cliente_id, f.desde, f.hasta, p_dia)
                             AND toca_visita(p_dia, f.dia_semana, f.semana_del_mes))
           END
$$;
