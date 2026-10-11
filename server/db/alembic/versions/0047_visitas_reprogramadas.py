"""La visita reprogramada

Revision ID: 0047_visitas_reprogramadas
Revises: 0046_familias_al_telefono

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0047_visitas_reprogramadas"
down_revision = "0046_familias_al_telefono"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0054_visitas_reprogramadas.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_visita_reprogramada ON no_drops")
    op.execute("DROP FUNCTION IF EXISTS fn_visita_reprogramada()")
    op.execute("ALTER TABLE clientes DROP COLUMN IF EXISTS visita_reprogramada")
    op.execute("ALTER TABLE no_drops DROP CONSTRAINT IF EXISTS reprogramada_despues")
    op.execute("ALTER TABLE no_drops DROP COLUMN IF EXISTS reprogramada_para")
