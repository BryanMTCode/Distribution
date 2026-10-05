"""Blindaje de la sincronización: cliente que cambia de ruta, y el piso de retención

Revision ID: 0027_blindaje_sync
Revises: 0026_catalogo_editable

Hallazgos 3 y 4 de la auditoría de sincronización de octubre de 2026. Ver el
encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0027_blindaje_sync"
down_revision = "0026_catalogo_editable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0034_blindaje_sync.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_cliente ON clientes")
    op.execute(
        "CREATE TRIGGER trg_cambio_cliente "
        " AFTER INSERT OR UPDATE OR DELETE ON clientes "
        " FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('cliente','id','ruta_id')"
    )
    op.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_cliente()")
    op.execute("DROP TABLE IF EXISTS sync_retencion")
