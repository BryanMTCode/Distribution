"""Mover mercancía de la bodega al camión, y de regreso, desde el panel

Revision ID: 0057_traspaso_bodega_camion
Revises: 0056_corte_automatico

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0057_traspaso_bodega_camion"
down_revision = "0056_corte_automatico"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0064_traspaso_bodega_camion.sql"))


def downgrade() -> None:
    # Un traspaso ya registrado no tiene a dónde volver: se queda la regla nueva.
    pass
