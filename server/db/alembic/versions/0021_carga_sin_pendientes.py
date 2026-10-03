"""La regla del §2.3: no se carga con operaciones pendientes

Revision ID: 0021_carga_sin_pendientes
Revises: 0020_compras
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0021_carga_sin_pendientes"
down_revision = "0020_compras"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0028_carga_sin_pendientes.sql"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cargas_forzadas")
    op.execute("ALTER TABLE cargas DROP COLUMN IF EXISTS forzada")
    op.execute("ALTER TABLE cargas DROP COLUMN IF EXISTS pendientes_al_confirmar")
    # Los renglones de `auditoria` NO se borran: son el registro de que alguien
    # forzó una carga, y eso ocurrió. Deshacer la migración no lo deshace.
