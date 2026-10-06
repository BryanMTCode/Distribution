"""El plan de visita: qué clientes tocan cada día

Revision ID: 0034_plan_de_visita
Revises: 0033_cambio_fisico

`clientes_frecuencia` existía desde la 0003 sin usarse. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0034_plan_de_visita"
down_revision = "0033_cambio_fisico"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0041_plan_de_visita.sql"))


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS toca_visita(date, smallint, smallint)")
    op.execute("DROP TRIGGER IF EXISTS trg_plan_visita_baja ON clientes_frecuencia")
    op.execute("DROP TRIGGER IF EXISTS trg_plan_visita_cambio ON clientes_frecuencia")
    op.execute("DROP TRIGGER IF EXISTS trg_plan_visita_alta ON clientes_frecuencia")
    op.execute("DROP FUNCTION IF EXISTS fn_plan_visita_al_cliente()")
    op.execute("ALTER TABLE clientes DROP COLUMN IF EXISTS plan_visita")
    op.execute("ALTER TABLE clientes_frecuencia DROP COLUMN IF EXISTS desde")
