"""Fase 9: borrado remoto del dispositivo

Revision ID: 0016_borrado_remoto
Revises: 0015_seguridad_por_renglon
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0016_borrado_remoto"
down_revision = "0015_seguridad_por_renglon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0023_borrado_remoto.sql"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dispositivos_borrado_pendiente")
    for restriccion in ("borrado_confirmado_tras_orden", "borrado_con_motivo"):
        op.execute(f"ALTER TABLE dispositivos DROP CONSTRAINT IF EXISTS {restriccion}")
    for columna in (
        "borrado_cola_al_confirmar",
        "borrado_confirmado_en",
        "borrado_motivo",
        "borrado_ordenado_por",
        "borrado_ordenado_en",
    ):
        op.execute(f"ALTER TABLE dispositivos DROP COLUMN IF EXISTS {columna}")
