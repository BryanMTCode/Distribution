"""Fase 8: el esquema estrella del laboratorio analítico

Revision ID: 0013_esquema_estrella
Revises: 0012_cola_reportada
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0013_esquema_estrella"
down_revision = "0012_cola_reportada"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0020_esquema_estrella.sql"))


def downgrade() -> None:
    # En orden inverso: las dimensiones no dependen de los hechos, pero los
    # índices sí de sus vistas, y DROP ... CASCADE se los lleva.
    for vista in (
        "fact_movimientos",
        "fact_visitas",
        "fact_ventas",
        "dim_ruta",
        "dim_vendedor",
        "dim_producto",
        "dim_cliente",
    ):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {vista} CASCADE")
    op.execute("DROP TABLE IF EXISTS analitica_refrescos")
    op.execute("DROP TABLE IF EXISTS dim_tiempo")
