"""Operaciones: todas juntas, y anular deshaciendo lo que movieron

Revision ID: 0052_operaciones_anuladas
Revises: 0051_revision_de_la_app

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0052_operaciones_anuladas"
down_revision = "0051_revision_de_la_app"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0059_operaciones_anuladas.sql"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS anulaciones")
    op.execute("DELETE FROM ajustes_camion WHERE tipo = 'anulacion'")
    op.execute("ALTER TABLE ajustes_camion DROP CONSTRAINT IF EXISTS ajustes_camion_tipo_check")
    op.execute(
        "ALTER TABLE ajustes_camion ADD CONSTRAINT ajustes_camion_tipo_check "
        "CHECK (tipo IN ('conteo', 'merma', 'entrada'))"
    )
    op.execute("DELETE FROM permisos WHERE codigo = 'operaciones.anular'")
