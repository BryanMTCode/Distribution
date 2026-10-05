"""La oficina puede cancelar y corregir una venta, y el teléfono se entera

Revision ID: 0024_venta_corregida
Revises: 0023_camion_rodante

Conecta `ventas_cancelaciones` (que existe desde la 0005 sin usarse), agrega la
huella de la corrección y publica la venta al teléfono cuando la oficina la
cambia. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0024_venta_corregida"
down_revision = "0023_camion_rodante"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0031_venta_corregida.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_venta ON ventas")
    op.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_venta()")
    op.execute("DROP INDEX IF EXISTS uq_cancelacion_venta")
    op.execute(
        "ALTER TABLE ventas DROP COLUMN IF EXISTS corregida_en, "
        "                   DROP COLUMN IF EXISTS corregida_por, "
        "                   DROP COLUMN IF EXISTS correccion_motivo"
    )
