"""Siembra en change_log los datos de referencia que el dispositivo nunca recibió

Revision ID: 0006_change_log_referencia
Revises: 0005_importes_rigidos
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0006_change_log_referencia"
down_revision = "0005_importes_rigidos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0013_sembrar_change_log_referencia.sql"))


def downgrade() -> None:
    # No se borra: un dispositivo que ya aplicó el delta no se desharía de la
    # lista, y quitar el renglón solo escondería el historial.
    pass
