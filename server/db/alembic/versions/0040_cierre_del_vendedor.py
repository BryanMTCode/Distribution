"""El cierre del vendedor: corte, solicitud de carga y su aceptación

Revision ID: 0040_cierre_del_vendedor
Revises: 0039_solo_contado

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0040_cierre_del_vendedor"
down_revision = "0039_solo_contado"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0047_cierre_del_vendedor.sql"))


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_cambio_solicitud_carga ON solicitudes_carga;
        DROP FUNCTION IF EXISTS fn_registrar_cambio_solicitud_carga();
        DROP TABLE IF EXISTS solicitud_carga_detalle;
        DROP TABLE IF EXISTS solicitudes_carga;
        DROP TABLE IF EXISTS corte_vendedor_conteo;
        DROP TABLE IF EXISTS cortes_vendedor;
        """
    )
