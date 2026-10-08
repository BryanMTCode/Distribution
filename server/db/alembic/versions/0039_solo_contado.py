"""Solo contado: la venta lleva su forma de pago

Revision ID: 0039_solo_contado
Revises: 0038_el_gerente_corta

Ver el encabezado del .sql.
"""

from __future__ import annotations

from alembic import op

from db.sql import leer_sql

revision = "0039_solo_contado"
down_revision = "0038_el_gerente_corta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cruda = op.get_bind().connection.driver_connection
    with cruda.cursor() as cursor:
        cursor.execute(leer_sql("0046_solo_contado.sql"))


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE tablero_dia
            DROP COLUMN venta_efectivo,
            DROP COLUMN venta_transferencia,
            ADD COLUMN venta_contado    numeric(14,2) NOT NULL DEFAULT 0,
            ADD COLUMN venta_credito    numeric(14,2) NOT NULL DEFAULT 0,
            ADD COLUMN cobrado_total    numeric(14,2) NOT NULL DEFAULT 0,
            ADD COLUMN cobrado_efectivo numeric(14,2) NOT NULL DEFAULT 0;
        DELETE FROM tablero_dia;
        CREATE TABLE tablero_cartera (
            id                  boolean PRIMARY KEY DEFAULT true CHECK (id),
            saldo_total         numeric(14,2) NOT NULL DEFAULT 0,
            saldo_vencido       numeric(14,2) NOT NULL DEFAULT 0,
            vencido_1_15        numeric(14,2) NOT NULL DEFAULT 0,
            vencido_16_30       numeric(14,2) NOT NULL DEFAULT 0,
            vencido_31_60       numeric(14,2) NOT NULL DEFAULT 0,
            vencido_61_mas      numeric(14,2) NOT NULL DEFAULT 0,
            facturas_abiertas   integer NOT NULL DEFAULT 0,
            facturas_vencidas   integer NOT NULL DEFAULT 0,
            clientes_con_saldo  integer NOT NULL DEFAULT 0,
            clientes_vencidos   integer NOT NULL DEFAULT 0,
            calculado_en        timestamptz NOT NULL DEFAULT now()
        );
        UPDATE motivos_no_drop SET activo = true WHERE codigo = 'SIN_CREDITO';
        DROP INDEX IF EXISTS uq_cuenta_vendedor_por_venta;
        ALTER TABLE cuenta_vendedor DROP CONSTRAINT cuenta_vendedor_con_respaldo;
        ALTER TABLE cuenta_vendedor ADD CONSTRAINT cuenta_vendedor_con_respaldo CHECK (
            CASE
              WHEN origen IN ('faltante_mercancia', 'merma_atribuible', 'faltante_efectivo')
                THEN liquidacion_id IS NOT NULL
              WHEN origen = 'cobro_no_entregado'
                THEN cobro_id IS NOT NULL AND registrado_por IS NOT NULL
              ELSE registrado_por IS NOT NULL
            END
        );
        ALTER TABLE cuenta_vendedor DROP CONSTRAINT cuenta_vendedor_origen_cuadra;
        ALTER TABLE cuenta_vendedor ADD CONSTRAINT cuenta_vendedor_origen_cuadra CHECK (
            (tipo = 'cargo' AND origen IN ('faltante_mercancia', 'merma_atribuible',
                                           'faltante_efectivo', 'cobro_no_entregado',
                                           'cargo_manual'))
         OR (tipo = 'abono' AND origen IN ('descuento_nomina', 'pago'))
         OR (tipo = 'condonacion' AND origen = 'condonacion')
        );
        ALTER TABLE cuenta_vendedor DROP COLUMN venta_id;
        DROP INDEX IF EXISTS idx_ventas_pago_por_confirmar;
        ALTER TABLE ventas
            DROP COLUMN pago_resolucion_nota,
            DROP COLUMN pago_resuelto_por,
            DROP COLUMN pago_resuelto_en,
            DROP COLUMN pago_estado,
            DROP COLUMN referencia_pago,
            DROP COLUMN forma_pago;
        """
    )
