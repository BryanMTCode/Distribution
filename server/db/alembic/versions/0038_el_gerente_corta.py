"""El gerente también hace el corte del día

Revision ID: 0038_el_gerente_corta
Revises: 0037_el_gerente_carga

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0038_el_gerente_corta"
down_revision = "0037_el_gerente_carga"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0045_el_gerente_corta.sql"))


def downgrade() -> None:
    op.execute(
        "DELETE FROM roles_permisos "
        " WHERE rol_codigo = 'gerente' AND permiso_codigo = 'inventario.liquidar'"
    )
