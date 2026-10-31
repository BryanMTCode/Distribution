"""El producto malo que el vendedor tiene que regresar a la bodega

Revision ID: 0068_producto_malo
Revises: 0067_boletos_del_panel

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0068_producto_malo"
down_revision = "0067_boletos_del_panel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0075_producto_malo.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS producto_malo_resuelto")
    op.execute("DROP INDEX IF EXISTS uq_almacenes_producto_malo")
    op.execute("ALTER TABLE almacenes DROP COLUMN IF EXISTS producto_malo")
