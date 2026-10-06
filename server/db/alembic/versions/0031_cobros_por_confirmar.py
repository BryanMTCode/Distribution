"""Transferencias y cheques por confirmar: no liberan crédito hasta el banco

Revision ID: 0031_cobros_por_confirmar
Revises: 0030_ajuste_de_bodega

Un cobro que no es efectivo nace `por_confirmar` y no se aplica a las facturas
hasta que la oficina lo confirma. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0031_cobros_por_confirmar"
down_revision = "0030_ajuste_de_bodega"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0038_cobros_por_confirmar.sql"))


def downgrade() -> None:
    # Bajar no puede aplicar en FIFO lo que quedó por confirmar: eso es una
    # decisión de la oficina. Se niega mientras haya alguno, en vez de dejar
    # cobros en un estado que el esquema anterior no conoce.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM cobros WHERE estado IN ('por_confirmar','rechazado')) THEN
            RAISE EXCEPTION 'hay cobros por confirmar o rechazados: resuélvelos antes de bajar';
          END IF;
        END $$;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_cartera_por_cobro_resuelto ON cobros")
    op.execute("DROP TRIGGER IF EXISTS trg_cartera_por_cobro_nuevo ON cobros")
    op.execute("DROP INDEX IF EXISTS idx_cobros_por_confirmar")
    op.execute("ALTER TABLE cobros DROP CONSTRAINT IF EXISTS cobro_sin_aplicar_hasta_confirmar")
    op.execute("ALTER TABLE cobros DROP CONSTRAINT IF EXISTS cobro_rechazo_con_motivo")
    op.execute("ALTER TABLE cobros DROP CONSTRAINT IF EXISTS cobro_efectivo_no_espera_banco")
    # La vista vieja no tiene `por_confirmar`, y una columna no se quita con
    # CREATE OR REPLACE: se tira y se vuelve a crear desde su archivo.
    op.execute("DROP VIEW v_cartera_cliente")
    cruda = op.get_bind().connection.driver_connection
    sql = leer_sql("0009_semilla_y_cartera.sql")
    inicio = sql.index("CREATE OR REPLACE VIEW v_cartera_cliente")
    fin = sql.index(";", inicio) + 1
    with cruda.cursor() as cursor:
        cursor.execute(sql[inicio:fin])
    op.execute(
        "ALTER TABLE cobros DROP COLUMN resolucion_nota, DROP COLUMN resuelto_por, "
        "DROP COLUMN resuelto_en"
    )
    op.execute("ALTER TABLE cobros DROP CONSTRAINT cobros_estado_check")
    op.execute(
        "ALTER TABLE cobros ADD CONSTRAINT cobros_estado_check "
        "CHECK (estado IN ('confirmado','cancelado'))"
    )
    op.execute("DELETE FROM roles_permisos WHERE permiso_codigo = 'cobranza.confirmar'")
    op.execute("DELETE FROM permisos WHERE codigo = 'cobranza.confirmar'")
