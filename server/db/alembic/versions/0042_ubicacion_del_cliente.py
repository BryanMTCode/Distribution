"""La ubicación del cliente, con GPS o escrita a mano

Revision ID: 0042_ubicacion_del_cliente
Revises: 0041_compra_desde_el_telefono

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0042_ubicacion_del_cliente"
down_revision = "0041_compra_desde_el_telefono"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0049_ubicacion_del_cliente.sql"))


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM roles_permisos WHERE permiso_codigo = 'clientes.ubicar';
        DELETE FROM permisos WHERE codigo = 'clientes.ubicar';
        """
    )
