"""El vendedor devuelve mercancía a la bodega, pasando por tránsito

Revision ID: 0029_devolucion_a_bodega
Revises: 0028_identidad_del_equipo

`traspasos` existía desde la 0004 sin que nada la escribiera. Aquí se conecta para
el sentido camión → bodega, que lo inicia el vendedor y la bodega confirma contando.
Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0029_devolucion_a_bodega"
down_revision = "0028_identidad_del_equipo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0036_devolucion_a_bodega.sql"))


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cambio_traspaso ON traspasos")
    op.execute("DROP FUNCTION IF EXISTS fn_registrar_cambio_traspaso()")
    op.execute("DROP INDEX IF EXISTS idx_traspasos_en_transito")
    op.execute("ALTER TABLE traspaso_detalle DROP COLUMN IF EXISTS cantidad_recibida")
    op.execute(
        "ALTER TABLE traspasos "
        "  DROP COLUMN IF EXISTS dispositivo_id, "
        "  DROP COLUMN IF EXISTS fecha_dispositivo, "
        "  DROP COLUMN IF EXISTS fecha_operativa, "
        "  DROP COLUMN IF EXISTS observaciones"
    )
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_traspaso")
