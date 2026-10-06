"""Cuando una ruta cambia de manos, sus clientes viajan con ella

Revision ID: 0035_el_alcance_viaja
Revises: 0034_plan_de_visita

Ver el encabezado del .sql: sin esto, el titular nuevo recibía la ruta vacía.
También: la cartera viaja con el cliente que cambia de ruta, y su disparador corre
como su dueño (en producción daba 500 al cambiar el crédito de un cliente).
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0035_el_alcance_viaja"
down_revision = "0034_plan_de_visita"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0042_el_alcance_viaja.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_alcance_de_ruta ON usuarios_rutas")
    op.execute("DROP FUNCTION IF EXISTS fn_alcance_de_ruta()")
    # `fn_cartera_por_condiciones` se queda con la versión corregida: regresar a
    # la de la 0011 devolvería el 500 del panel en producción.
