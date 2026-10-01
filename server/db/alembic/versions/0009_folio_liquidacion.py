"""El consecutivo del folio de liquidación

Revision ID: 0009_folio_liquidacion
Revises: 0008_delta_de_carga
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0009_folio_liquidacion"
down_revision = "0008_delta_de_carga"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0016_folio_liquidacion.sql"))


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_liquidacion")
