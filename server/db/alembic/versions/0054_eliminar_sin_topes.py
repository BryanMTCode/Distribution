"""Eliminar sin topes: el corte también se deshace

Revision ID: 0054_eliminar_sin_topes
Revises: 0053_codigos_desde_1934

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0054_eliminar_sin_topes"
down_revision = "0053_codigos_desde_1934"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0061_eliminar_sin_topes.sql"))


def downgrade() -> None:
    # Un corte anulado no tiene a dónde volver: se queda la regla nueva.
    pass
