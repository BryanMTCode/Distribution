"""Importes rígidos: la aritmética de la partida como constraint

Revision ID: 0005_importes_rigidos
Revises: 0004_delta_cartera
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0005_importes_rigidos"
down_revision = "0004_delta_cartera"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0012_importes_rigidos.sql"))


def downgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE venta_partidas DROP CONSTRAINT IF EXISTS partida_importe_coherente"
        )
        cursor.execute(
            "ALTER TABLE venta_partidas "
            "DROP CONSTRAINT IF EXISTS partida_cantidad_base_coherente"
        )
