"""La tienda que el teléfono tiene, aunque el alcance no la deje ver

Revision ID: 0060_la_tienda_del_telefono
Revises: 0059_no_drop_sin_gps

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0060_la_tienda_del_telefono"
down_revision = "0059_no_drop_sin_gps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0067_la_tienda_que_el_telefono_tiene.sql"))


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_cliente_del_telefono(uuid)")
