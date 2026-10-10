"""Las familias de artículos llegan al teléfono

Revision ID: 0046_familias_al_telefono
Revises: 0045_telefono_desde_cero

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0046_familias_al_telefono"
down_revision = "0045_telefono_desde_cero"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0053_familias_al_telefono.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_categoria ON categorias")
