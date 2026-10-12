"""La revisión de la app, punto por punto

Revision ID: 0051_revision_de_la_app
Revises: 0050_objetivos_diarios

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0051_revision_de_la_app"
down_revision = "0050_objetivos_diarios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0058_revision_de_la_app.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS revision_app")
