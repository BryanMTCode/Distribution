"""Delta de cartera para el dispositivo

Revision ID: 0004_delta_cartera
Revises: 0003_change_log
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0004_delta_cartera"
down_revision = "0003_change_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0011_delta_cartera.sql"))


def downgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS trg_cartera_por_condiciones ON clientes")
        cursor.execute("DROP TRIGGER IF EXISTS trg_cambio_cartera ON cuentas_por_cobrar")
        cursor.execute("DROP FUNCTION IF EXISTS fn_cartera_por_condiciones()")
        cursor.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_cartera()")
