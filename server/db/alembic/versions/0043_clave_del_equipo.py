"""La clave corta para vincular el teléfono

Revision ID: 0043_clave_del_equipo
Revises: 0042_ubicacion_del_cliente

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0043_clave_del_equipo"
down_revision = "0042_ubicacion_del_cliente"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0050_clave_del_equipo.sql"))


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS uq_dispositivos_clave_vinculo;
        ALTER TABLE dispositivos DROP COLUMN IF EXISTS clave_vinculo;
        """
    )
