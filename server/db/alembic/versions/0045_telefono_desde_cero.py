"""Que el teléfono se entere de que la base se puso en blanco

Revision ID: 0045_telefono_desde_cero
Revises: 0044_efectivo_recibido

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0045_telefono_desde_cero"
down_revision = "0044_efectivo_recibido"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0052_telefono_desde_cero.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE dispositivos DROP COLUMN IF EXISTS empezar_de_cero")
