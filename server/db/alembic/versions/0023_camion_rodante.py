"""El camión es un almacén rodante: el saldo inicial entra en la ecuación

Revision ID: 0023_camion_rodante
Revises: 0022_disparadores_definer

Decisión de negocio de octubre 2026: la mercancía que no se vende se queda a
dormir en el camión y se acumula con la carga del día siguiente, así que no se le
puede cobrar como faltante al vendedor. Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0023_camion_rodante"
down_revision = "0022_disparadores_definer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0030_camion_rodante.sql"))


def downgrade() -> None:
    # Se baja la ecuación y el nombre de la columna. El disparador NO se
    # restaura al de 0015: volvería a publicar la carga liquidada sin ajustes, y
    # un teléfono con la app nueva se quedaría sin la corrección del cierre.
    # Bajar esta migración exige bajar también la app.
    op.execute("DROP INDEX IF EXISTS idx_liquidacion_faltantes")
    op.execute("ALTER TABLE liquidacion_detalle DROP COLUMN diferencia")
    op.execute(
        "ALTER TABLE liquidacion_detalle RENAME COLUMN cant_contada TO cant_retornada"
    )
    op.execute(
        """
        ALTER TABLE liquidacion_detalle
            ADD COLUMN diferencia numeric(14,3)
            GENERATED ALWAYS AS (
                cant_retornada
                - (cant_cargada - cant_vendida - cant_merma + cant_devuelta)
            ) STORED
        """
    )
    op.execute(
        "CREATE INDEX idx_liquidacion_faltantes "
        "ON liquidacion_detalle(liquidacion_id) WHERE diferencia <> 0"
    )
    op.execute("ALTER TABLE liquidacion_detalle DROP COLUMN cant_inicial")
