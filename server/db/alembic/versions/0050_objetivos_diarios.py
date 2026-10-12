"""El objetivo diario fijo: «por día 3,500, hasta que yo lo modifique»

Revision ID: 0050_objetivos_diarios
Revises: 0049_ventas_de_mayoreo

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0050_objetivos_diarios"
down_revision = "0049_ventas_de_mayoreo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0057_objetivos_diarios.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS objetivos_diarios")
