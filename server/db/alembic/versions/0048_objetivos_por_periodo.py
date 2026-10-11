"""Objetivos de cualquier periodo, incluso de un día

Revision ID: 0048_objetivos_por_periodo
Revises: 0047_visitas_reprogramadas

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0048_objetivos_por_periodo"
down_revision = "0047_visitas_reprogramadas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0055_objetivos_por_periodo.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS objetivos")
