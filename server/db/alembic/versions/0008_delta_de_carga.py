"""El delta de carga lleva su detalle, y el borrador no se publica

Revision ID: 0008_delta_de_carga
Revises: 0007_codigo_cliente
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0008_delta_de_carga"
down_revision = "0007_codigo_cliente"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0015_delta_de_carga.sql"))


def downgrade() -> None:
    # No se revierte la función: volver a la versión que publicaba borradores sin
    # detalle dejaría al dispositivo aplicando cargas que no puede usar. Si hace
    # falta deshacer, se escribe una migración nueva con la forma que se quiera.
    op.execute("DROP SEQUENCE IF EXISTS seq_folio_carga")
