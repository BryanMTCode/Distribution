"""Datos de referencia y vista de cartera

Revision ID: 0002_semilla_cartera
Revises: 0001_linea_base
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0002_semilla_cartera"
down_revision = "0001_linea_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0009_semilla_y_cartera.sql"))


def downgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute("DROP VIEW IF EXISTS v_cartera_cliente")
    # Los datos de referencia no se borran al revertir: hay documentos que los
    # referencian por llave foránea, y una migración que rompe integridad es
    # peor que una que deja filas de más.
