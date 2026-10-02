"""Salidas de bodega: ajuste por conteo y merma

Revision ID: 0019_salidas_ajuste
Revises: 0018_entradas_mercancia
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0019_salidas_ajuste"
down_revision = "0018_entradas_mercancia"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0026_salidas_ajuste.sql"))


def downgrade() -> None:
    # El permiso no se toca: lo otorgó la 0025 y la entrada sigue usándolo.
    op.execute("DROP TABLE IF EXISTS salida_detalle")
    op.execute("DROP TABLE IF EXISTS salidas")
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_salida")
