"""El teléfono reporta cuántas operaciones le quedan

Revision ID: 0012_cola_reportada
Revises: 0011_motivos_de_revision
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0012_cola_reportada"
down_revision = "0011_motivos_de_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0019_cola_reportada.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE dispositivos DROP COLUMN IF EXISTS cola_reportada_en")
    op.execute("ALTER TABLE dispositivos DROP COLUMN IF EXISTS cola_pendiente")
