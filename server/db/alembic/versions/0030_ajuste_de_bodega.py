"""El ajuste manual también para bodegas, sin que llegue a ningún teléfono

Revision ID: 0030_ajuste_de_bodega
Revises: 0029_devolucion_a_bodega

El disparador de `ajustes_camion` publicaba con el responsable del almacén, y una
bodega no tiene: `vendedor_id` nulo significa «a todos los teléfonos». Ver el
encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0030_ajuste_de_bodega"
down_revision = "0029_devolucion_a_bodega"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0037_ajuste_de_bodega.sql"))


def downgrade() -> None:
    # La versión de la 0032, que publicaba para cualquier almacén. Se restaura
    # releyendo su archivo para no duplicar su cuerpo aquí.
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0032_ajuste_del_camion.sql"))
