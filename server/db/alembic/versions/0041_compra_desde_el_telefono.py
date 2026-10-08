"""La compra que el gerente recibe desde el teléfono, aunque no haya señal

Revision ID: 0041_compra_desde_el_telefono
Revises: 0040_cierre_del_vendedor

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0041_compra_desde_el_telefono"
down_revision = "0040_cierre_del_vendedor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0048_compra_desde_el_telefono.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE entradas DROP COLUMN IF EXISTS costo_opcional")
