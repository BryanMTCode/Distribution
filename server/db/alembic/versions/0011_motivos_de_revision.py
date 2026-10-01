"""Las mermas y los no-drops decían "revísame" sin decir qué revisar

Revision ID: 0011_motivos_de_revision
Revises: 0010_catalogos_de_motivos
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0011_motivos_de_revision"
down_revision = "0010_catalogos_de_motivos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0018_motivos_de_revision.sql"))


def downgrade() -> None:
    op.execute("ALTER TABLE no_drops DROP COLUMN IF EXISTS revision_motivos")
    op.execute("ALTER TABLE mermas DROP COLUMN IF EXISTS revision_motivos")
