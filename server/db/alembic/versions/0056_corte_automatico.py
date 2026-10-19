"""El corte del vendedor se cierra solo

Revision ID: 0056_corte_automatico
Revises: 0055_codigos_anteriores

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0056_corte_automatico"
down_revision = "0055_codigos_anteriores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0063_corte_automatico.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE cortes_vendedor DROP COLUMN IF EXISTS no_se_cerro_solo")
