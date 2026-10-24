"""El plan de visita tiene fin: recorrer los días sin tocar lo trabajado

Revision ID: 0063_recorrer_los_dias
Revises: 0062_tiendas_ajenas

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0063_recorrer_los_dias"
down_revision = "0062_tiendas_ajenas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0070_recorrer_los_dias.sql"))


def downgrade() -> None:
    # Lo cerrado se va: sin `hasta`, el plan sería el viejo y el nuevo a la vez.
    op.execute("DELETE FROM clientes_frecuencia WHERE hasta IS NOT NULL")
    # Las dos funciones vuelven a como las dejaron la 0041 y la 0067.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_plan_visita_al_cliente() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE clientes c
               SET plan_visita = p.plan, actualizado_en = now()
              FROM (SELECT a.cliente_id,
                           COALESCE((SELECT jsonb_agg(jsonb_build_object(
                                              'dia', f.dia_semana, 'semana', f.semana_del_mes)
                                            ORDER BY f.dia_semana, f.semana_del_mes NULLS FIRST)
                                       FROM clientes_frecuencia f
                                      WHERE f.cliente_id = a.cliente_id), '[]'::jsonb) AS plan
                      FROM (SELECT DISTINCT cliente_id FROM afectados) a) p
             WHERE c.id = p.cliente_id AND c.plan_visita IS DISTINCT FROM p.plan;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_le_toca_visita(p_cliente uuid, p_dia date)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT CASE
                     WHEN EXISTS (SELECT 1 FROM no_drops n WHERE n.cliente_id = p_cliente
                                     AND n.reprogramada_para = p_dia) THEN true
                     WHEN NOT EXISTS (SELECT 1 FROM clientes_frecuencia f
                                       WHERE f.cliente_id = p_cliente) THEN NULL
                     ELSE EXISTS (SELECT 1 FROM clientes_frecuencia f
                                   WHERE f.cliente_id = p_cliente
                                     AND toca_visita(p_dia, f.dia_semana, f.semana_del_mes))
                   END
        $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS frecuencia_vigente(uuid, date, date, date)")
    op.execute("ALTER TABLE clientes_frecuencia DROP CONSTRAINT IF EXISTS ck_frecuencia_hasta")
    op.execute("DROP INDEX IF EXISTS uq_cliente_frecuencia")
    op.execute(
        "CREATE UNIQUE INDEX uq_cliente_frecuencia ON clientes_frecuencia "
        "(cliente_id, dia_semana, COALESCE(semana_del_mes::integer, 0))"
    )
    op.execute("ALTER TABLE clientes_frecuencia DROP COLUMN IF EXISTS hasta")
