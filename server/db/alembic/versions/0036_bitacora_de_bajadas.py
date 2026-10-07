"""La bitácora de lo que cada teléfono bajó

Revision ID: 0036_bitacora_de_bajadas
Revises: 0035_el_alcance_viaja

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0036_bitacora_de_bajadas"
down_revision = "0035_el_alcance_viaja"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0043_bitacora_de_bajadas.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sync_bajadas")
