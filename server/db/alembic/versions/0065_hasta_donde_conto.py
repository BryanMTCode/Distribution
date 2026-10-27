"""Hasta qué día contó cada corte

Revision ID: 0065_hasta_donde_conto
Revises: 0064_costo_y_ganancia

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0065_hasta_donde_conto"
down_revision = "0064_costo_y_ganancia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0072_hasta_donde_conto.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE liquidaciones DROP COLUMN IF EXISTS cuenta_hasta")
