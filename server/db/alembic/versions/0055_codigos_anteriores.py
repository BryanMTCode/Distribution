"""El código de un usuario se puede cambiar, y el viejo queda reservado

Revision ID: 0055_codigos_anteriores
Revises: 0054_eliminar_sin_topes

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0055_codigos_anteriores"
down_revision = "0054_eliminar_sin_topes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0062_codigos_anteriores.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS usuarios_codigos_anteriores")
