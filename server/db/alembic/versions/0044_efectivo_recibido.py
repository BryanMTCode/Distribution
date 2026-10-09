"""El efectivo que el gerente recibe al cerrar el corte

Revision ID: 0044_efectivo_recibido
Revises: 0043_clave_del_equipo

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0044_efectivo_recibido"
down_revision = "0043_clave_del_equipo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0051_efectivo_recibido.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE cortes_vendedor DROP COLUMN IF EXISTS efectivo_recibido")
