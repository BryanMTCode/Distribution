"""Las tiendas que no son del vendedor se van de su teléfono

Revision ID: 0062_tiendas_ajenas
Revises: 0061_ubicar_sin_ruta

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0062_tiendas_ajenas"
down_revision = "0061_ubicar_sin_ruta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0069_las_tiendas_que_no_son_suyas.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_telefono_de_otro_suelta_lo_ajeno ON dispositivos")
    op.execute("DROP TRIGGER IF EXISTS trg_telefono_nuevo_suelta_lo_ajeno ON dispositivos")
    op.execute("DROP FUNCTION IF EXISTS fn_telefono_nuevo_suelta_lo_ajeno()")
    op.execute("DROP FUNCTION IF EXISTS fn_soltar_tiendas_ajenas(uuid)")
    op.execute("DROP FUNCTION IF EXISTS fn_tiendas_que_no_son_suyas(uuid[])")
    op.execute(
        "ALTER TABLE dispositivos DROP COLUMN IF EXISTS tiendas_en_telefono, "
        "DROP COLUMN IF EXISTS tiendas_soltadas, DROP COLUMN IF EXISTS tiendas_revisadas_en"
    )
    # El de la 0034: solo avisaba a la ruta que perdía al cliente si tenía ruta.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_registrar_cambio_cliente() RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
        BEGIN
            v_registro := v_registro - 'ubicacion';
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
        """
    )
