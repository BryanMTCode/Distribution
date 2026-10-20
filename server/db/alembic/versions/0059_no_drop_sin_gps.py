"""La visita sin venta, también sin GPS

Revision ID: 0059_no_drop_sin_gps
Revises: 0058_borrar_con_rls

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0059_no_drop_sin_gps"
down_revision = "0058_borrar_con_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0066_no_drop_sin_gps.sql"))


def downgrade() -> None:
    # Solo se puede volver si no entró ninguno sin GPS.
    op.execute("ALTER TABLE no_drops DROP CONSTRAINT IF EXISTS no_drop_lat_lng_juntas")
    op.execute("ALTER TABLE no_drops ALTER COLUMN lat SET NOT NULL")
    op.execute("ALTER TABLE no_drops ALTER COLUMN lng SET NOT NULL")
