"""Los catálogos de motivos nunca llegaban al teléfono

Revision ID: 0010_catalogos_de_motivos
Revises: 0009_folio_liquidacion
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0010_catalogos_de_motivos"
down_revision = "0009_folio_liquidacion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0017_catalogos_de_motivos.sql"))


def downgrade() -> None:
    # Los renglones del change_log no se borran: un dispositivo que ya los aplicó
    # no se desharía de ellos, y quitarlos solo escondería el historial.
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_motivo_merma ON motivos_merma")
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_motivo_no_drop ON motivos_no_drop")
