"""Fase 7: modelos de lectura del tablero de Gerencia

Revision ID: 0014_tablero_gerencia
Revises: 0013_esquema_estrella
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0014_tablero_gerencia"
down_revision = "0013_esquema_estrella"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0021_tablero_gerencia.sql"))


def downgrade() -> None:
    op.execute(
        "DELETE FROM roles_permisos "
        " WHERE permiso_codigo IN ('tablero.ver','objetivos.administrar')"
    )
    op.execute(
        "DELETE FROM permisos WHERE codigo IN ('tablero.ver','objetivos.administrar')"
    )
    for tabla in (
        "tablero_refrescos",
        "tablero_cartera",
        "tablero_mes_ruta",
        "tablero_dia",
        "objetivos_ruta",
    ):
        op.execute(f"DROP TABLE IF EXISTS {tabla}")
