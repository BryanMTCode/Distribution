"""La cuenta del vendedor: cargos a costo, abonos y condonación

Revision ID: 0032_cuenta_del_vendedor
Revises: 0031_cobros_por_confirmar

Lo que el Corte del día calculaba y nadie acumulaba. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0032_cuenta_del_vendedor"
down_revision = "0031_cobros_por_confirmar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0039_cuenta_del_vendedor.sql"))


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_cuenta_vendedor")
    op.execute("DROP TABLE IF EXISTS cuenta_vendedor")
    op.execute("ALTER TABLE liquidaciones DROP COLUMN IF EXISTS arqueo_en")
    op.execute(
        "DELETE FROM roles_permisos WHERE permiso_codigo IN "
        "('vendedores.cuenta_ver', 'vendedores.cuenta_mover')"
    )
    op.execute(
        "DELETE FROM permisos WHERE codigo IN "
        "('vendedores.cuenta_ver', 'vendedores.cuenta_mover')"
    )
