"""El cambio físico: fresco por caducado, sin dinero de por medio

Revision ID: 0033_cambio_fisico
Revises: 0032_cuenta_del_vendedor

Un tipo más de documento en `mermas`. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0033_cambio_fisico"
down_revision = "0032_cuenta_del_vendedor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0040_cambio_fisico.sql"))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM mermas WHERE tipo = 'cambio') THEN
            RAISE EXCEPTION 'hay cambios físicos registrados: no se puede bajar';
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE mermas DROP CONSTRAINT devolucion_requiere_cliente")
    op.execute(
        "ALTER TABLE mermas ADD CONSTRAINT devolucion_requiere_cliente "
        "CHECK (tipo <> 'devolucion_cliente' OR cliente_id IS NOT NULL)"
    )
    op.execute("ALTER TABLE mermas DROP CONSTRAINT mermas_tipo_check")
    op.execute(
        "ALTER TABLE mermas ADD CONSTRAINT mermas_tipo_check "
        "CHECK (tipo IN ('merma', 'devolucion_cliente'))"
    )
