"""Poblado del change_log por trigger

Revision ID: 0003_change_log
Revises: 0002_semilla_cartera
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0003_change_log"
down_revision = "0002_semilla_cartera"
branch_labels = None
depends_on = None

TRIGGERS = [
    ("trg_cambio_producto", "productos"),
    ("trg_cambio_producto_unidad", "producto_unidades"),
    ("trg_cambio_precio", "precios"),
    ("trg_cambio_lista_precios", "listas_precios"),
    ("trg_cambio_promocion", "promociones"),
    ("trg_cambio_cliente", "clientes"),
    ("trg_cambio_carga", "cargas"),
]


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0010_change_log_triggers.sql"))


def downgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        for trigger, tabla in TRIGGERS:
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {tabla}")
        cursor.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_carga()")
        cursor.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio()")
