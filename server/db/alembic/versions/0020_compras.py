"""Compras: proveedores, costo promedio y cuentas por pagar

Revision ID: 0020_compras
Revises: 0019_salidas_ajuste
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0020_compras"
down_revision = "0019_salidas_ajuste"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0027_compras.sql"))


def downgrade() -> None:
    op.execute(
        "DELETE FROM roles_permisos "
        " WHERE permiso_codigo IN ('compras.administrar','compras.pagar')"
    )
    op.execute(
        "DELETE FROM permisos WHERE codigo IN ('compras.administrar','compras.pagar')"
    )
    op.execute("DROP TABLE IF EXISTS pagos_proveedor")
    op.execute("DROP TABLE IF EXISTS cuentas_por_pagar")
    op.execute("DROP TABLE IF EXISTS producto_costos")
    # Las columnas que la 0027 agregó a las entradas, antes de la tabla a la
    # que apuntan.
    op.execute("ALTER TABLE entrada_detalle DROP COLUMN IF EXISTS importe")
    op.execute("ALTER TABLE entrada_detalle DROP COLUMN IF EXISTS costo_unitario")
    op.execute("ALTER TABLE entradas DROP COLUMN IF EXISTS importe_total")
    op.execute("ALTER TABLE entradas DROP COLUMN IF EXISTS proveedor_id")
    op.execute("DROP TABLE IF EXISTS proveedores")
