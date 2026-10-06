"""El camión asignado viaja al teléfono

Revision ID: 0028_identidad_del_equipo
Revises: 0027_blindaje_sync

Hallazgo 5 de la auditoría de sincronización: `almacen_id` vivía solo en la
credencial, que se reescribe únicamente con un login en línea. Ver el encabezado
del .sql — y en particular por qué el payload se arma a mano.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0028_identidad_del_equipo"
down_revision = "0027_blindaje_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0035_identidad_del_equipo.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_identidad ON usuarios")
    op.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_identidad()")
