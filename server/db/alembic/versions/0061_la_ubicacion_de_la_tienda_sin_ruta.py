"""La ubicación de una tienda sin ruta, corregida desde el teléfono

Revision ID: 0061_ubicar_sin_ruta
Revises: 0060_la_tienda_del_telefono

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0061_ubicar_sin_ruta"
down_revision = "0060_la_tienda_del_telefono"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0068_la_ubicacion_de_la_tienda_sin_ruta.sql"))


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "fn_ubicar_tienda_sin_ruta(uuid, numeric, numeric, text, numeric, timestamptz)"
    )
