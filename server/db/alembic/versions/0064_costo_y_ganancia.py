"""El costo de lo que se vende, y quién ve el costo y la ganancia

Revision ID: 0064_costo_y_ganancia
Revises: 0063_recorrer_los_dias

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0064_costo_y_ganancia"
down_revision = "0063_recorrer_los_dias"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0071_costo_y_ganancia.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_costo_de_lo_vendido ON venta_mayoreo_partidas")
    op.execute("DROP TRIGGER IF EXISTS trg_costo_de_lo_vendido ON venta_partidas")
    op.execute("DROP FUNCTION IF EXISTS fn_costo_de_lo_vendido()")
    op.execute("DROP TABLE IF EXISTS costo_de_lo_vendido_mayoreo")
    op.execute("DROP TABLE IF EXISTS costo_de_lo_vendido")
    op.execute("DELETE FROM usuarios_permisos WHERE permiso_codigo = 'costos.ver'")
    op.execute("DELETE FROM roles_permisos WHERE permiso_codigo = 'costos.ver'")
    op.execute("DELETE FROM permisos WHERE codigo = 'costos.ver'")
