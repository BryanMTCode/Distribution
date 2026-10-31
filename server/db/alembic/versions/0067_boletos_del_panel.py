"""El boleto para entrar al dashboard desde la app de la oficina

Revision ID: 0067_boletos_del_panel
Revises: 0066_tarjeta_de_la_empresa

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0067_boletos_del_panel"
down_revision = "0066_tarjeta_de_la_empresa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0074_boletos_del_panel.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS boletos_panel")
