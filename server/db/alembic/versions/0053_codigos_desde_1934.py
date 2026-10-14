"""Las tiendas nuevas siguen en 1934

Revision ID: 0053_codigos_desde_1934
Revises: 0052_operaciones_anuladas

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0053_codigos_desde_1934"
down_revision = "0052_operaciones_anuladas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0060_codigos_desde_1934.sql"))


def downgrade() -> None:
    # La secuencia no se regresa: bajar el número podría repetir un código.
    pass
