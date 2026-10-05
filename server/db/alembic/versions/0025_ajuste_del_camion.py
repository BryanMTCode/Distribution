"""La oficina puede ajustar el inventario de un camión

Revision ID: 0025_ajuste_del_camion
Revises: 0024_venta_corregida

Excepción documentada a §0.2: el saldo lo escribe la oficina y el delta firmado
viaja al teléfono para que el dueño del almacén converja. Ver el encabezado del
.sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0025_ajuste_del_camion"
down_revision = "0024_venta_corregida"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0032_ajuste_del_camion.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ajuste_camion ON ajustes_camion")
    op.execute("DROP FUNCTION IF EXISTS fn_registrar_ajuste_camion()")
    op.execute("DROP TABLE IF EXISTS ajustes_camion")
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_ajuste_camion")
