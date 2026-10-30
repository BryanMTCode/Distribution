"""La tarjeta de débito de la empresa

Revision ID: 0066_tarjeta_de_la_empresa
Revises: 0065_hasta_donde_conto

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0066_tarjeta_de_la_empresa"
down_revision = "0065_hasta_donde_conto"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0073_tarjeta_de_la_empresa.sql"))


def downgrade() -> None:
    # El pago no se borra: sale de la cuenta del banco, como una transferencia.
    op.execute("UPDATE pagos_proveedor SET forma_pago = 'transferencia' "
               "WHERE forma_pago = 'tarjeta'")
    op.execute("ALTER TABLE pagos_proveedor DROP CONSTRAINT IF EXISTS "
               "pagos_proveedor_forma_pago_check")
    op.execute("ALTER TABLE pagos_proveedor ADD CONSTRAINT pagos_proveedor_forma_pago_check "
               "CHECK (forma_pago IN ('efectivo', 'transferencia', 'cheque'))")
    op.execute("DROP TABLE IF EXISTS movimientos_tarjeta")
    op.execute("DELETE FROM permisos WHERE codigo = 'tarjeta.ver'")
