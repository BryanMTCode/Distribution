"""Entradas de mercancía a la bodega

Revision ID: 0018_entradas_mercancia
Revises: 0017_piloto
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0018_entradas_mercancia"
down_revision = "0017_piloto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0025_entradas_mercancia.sql"))


def downgrade() -> None:
    # El permiso NO se borra —viene de la migración 0009— pero sí se quita el
    # GRANT que esta migración le dio al supervisor.
    op.execute(
        "DELETE FROM roles_permisos "
        " WHERE rol_codigo = 'supervisor' AND permiso_codigo = 'inventario.ajustar'"
    )
    op.execute("DROP TABLE IF EXISTS entrada_detalle")
    op.execute("DROP TABLE IF EXISTS entradas")
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_entrada")
