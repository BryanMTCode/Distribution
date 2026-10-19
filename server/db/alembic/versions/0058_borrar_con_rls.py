"""Lo que el panel borra, que con el rol de producción no se borraba

Revision ID: 0058_borrar_con_rls
Revises: 0057_traspaso_bodega_camion

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0058_borrar_con_rls"
down_revision = "0057_traspaso_bodega_camion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0065_borrar_con_rls.sql"))


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS no_drops_baja_de_oficina ON no_drops")
    op.execute("DROP POLICY IF EXISTS clientes_baja_de_oficina ON clientes")
    op.execute("DROP FUNCTION IF EXISTS fn_olvidar_avisos_del_usuario(uuid)")
