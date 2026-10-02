"""Fase 3: el instrumento del piloto de campo

Revision ID: 0017_piloto
Revises: 0016_borrado_remoto
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0017_piloto"
down_revision = "0016_borrado_remoto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0024_piloto.sql"))


def downgrade() -> None:
    op.execute(
        "DELETE FROM roles_permisos WHERE permiso_codigo = 'piloto.administrar'"
    )
    op.execute("DELETE FROM permisos WHERE codigo = 'piloto.administrar'")
    # El orden importa: las tres cuelgan de `pilotos` por llave foránea.
    for tabla in (
        "piloto_criterios",
        "piloto_incidencias",
        "piloto_jornadas",
        "pilotos",
    ):
        op.execute(f"DROP TABLE IF EXISTS {tabla}")
