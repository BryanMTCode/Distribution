"""El consecutivo del código de cliente: sin él un prospecto se queda prospecto

Revision ID: 0007_codigo_cliente
Revises: 0006_change_log_referencia
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0007_codigo_cliente"
down_revision = "0006_change_log_referencia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0014_codigo_cliente.sql"))


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS seq_codigo_cliente")
