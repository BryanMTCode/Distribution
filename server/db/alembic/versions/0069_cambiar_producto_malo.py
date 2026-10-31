"""El producto malo se cambia por bueno en la bodega

Revision ID: 0069_cambiar_producto_malo
Revises: 0068_producto_malo

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0069_cambiar_producto_malo"
down_revision = "0068_producto_malo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0076_cambiar_producto_malo.sql"))


def downgrade() -> None:
    op.execute("DELETE FROM producto_malo_resuelto WHERE como = 'cambiado'")
    op.execute(
        "ALTER TABLE producto_malo_resuelto DROP CONSTRAINT IF EXISTS "
        "producto_malo_resuelto_como_check"
    )
    op.execute(
        "ALTER TABLE producto_malo_resuelto ADD CONSTRAINT producto_malo_resuelto_como_check "
        "CHECK (como IN ('recibido', 'cargado'))"
    )
    op.execute(
        "ALTER TABLE producto_malo_resuelto DROP CONSTRAINT IF EXISTS producto_malo_con_respaldo"
    )
    op.execute(
        "ALTER TABLE producto_malo_resuelto ADD CONSTRAINT producto_malo_con_respaldo CHECK ("
        "(como = 'recibido' AND ajuste_id IS NOT NULL) "
        "OR (como = 'cargado' AND cargo_id IS NOT NULL))"
    )
