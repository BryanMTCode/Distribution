"""El gerente también carga camiones

Revision ID: 0037_el_gerente_carga
Revises: 0036_bitacora_de_bajadas

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0037_el_gerente_carga"
down_revision = "0036_bitacora_de_bajadas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0044_el_gerente_carga.sql"))


def downgrade() -> None:
    op.execute(
        "DELETE FROM roles_permisos "
        " WHERE rol_codigo = 'gerente' AND permiso_codigo = 'inventario.cargar'"
    )
