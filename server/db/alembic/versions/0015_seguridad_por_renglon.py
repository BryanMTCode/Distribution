"""Fase 9: seguridad por renglón (RLS)

Revision ID: 0015_seguridad_por_renglon
Revises: 0014_tablero_gerencia
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0015_seguridad_por_renglon"
down_revision = "0014_tablero_gerencia"
branch_labels = None
depends_on = None

# Las tablas que quedan con políticas. La lista vive aquí y en el .sql; el
# downgrade la necesita para apagarlas en bloque.
TABLAS = (
    "clientes",
    "ventas",
    "venta_partidas",
    "cobros",
    "cobros_aplicaciones",
    "cuentas_por_cobrar",
    "no_drops",
    "mermas",
    "merma_detalle",
    "change_log",
)


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0022_seguridad_por_renglon.sql"))


def downgrade() -> None:
    # Se apaga RLS antes de tirar las funciones: una política que referencia
    # una función inexistente deja la tabla ilegible para el rol restringido.
    for tabla in TABLAS:
        op.execute(f"ALTER TABLE {tabla} DISABLE ROW LEVEL SECURITY")
    for funcion in ("dsd_ve_todo", "dsd_rutas", "dsd_usuario", "dsd_rol"):
        op.execute(f"DROP FUNCTION IF EXISTS {funcion}() CASCADE")
