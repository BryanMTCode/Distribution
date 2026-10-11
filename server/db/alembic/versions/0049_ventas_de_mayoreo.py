"""Ventas de mayoreo de la oficina

Revision ID: 0049_ventas_de_mayoreo
Revises: 0048_objetivos_por_periodo

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0049_ventas_de_mayoreo"
down_revision = "0048_objetivos_por_periodo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0056_ventas_de_mayoreo.sql"))


def downgrade() -> None:
    op.execute("DELETE FROM roles_permisos WHERE permiso_codigo = 'mayoreo.vender'")
    op.execute("DELETE FROM permisos WHERE codigo = 'mayoreo.vender'")
    op.execute("DROP TABLE IF EXISTS venta_mayoreo_partidas")
    op.execute("DROP TABLE IF EXISTS ventas_mayoreo")
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_mayoreo")
    op.execute("ALTER TABLE clientes DROP COLUMN IF EXISTS mayoreo")
