-- =============================================================================
-- 0046 · Solo contado: toda venta se paga en el acto
-- =============================================================================
-- Retroalimentación del piloto (octubre 2026): la operación es estrictamente de
-- contado. Se acaban los límites de crédito, la deuda de los clientes, los abonos
-- a notas pasadas y la cartera vencida. Toda venta se paga al entregar, en
-- efectivo o por transferencia.
--
-- La forma de pago vive ahora EN LA VENTA. Antes, una transferencia solo existía
-- como cobro (abono a una cuenta por cobrar); con contado no hay cuenta a la cual
-- abonar, y el dinero de la venta es la venta misma.
--
--   efectivo        nace 'confirmado': está en la mano y lo cuenta el arqueo.
--   transferencia   nace 'por_confirmar'. La oficina la compara contra el banco
--                   y la confirma o la marca «no llegó». Es SOLO para cuadrar el
--                   dinero: ya no hay saldo que liberar.
--
-- LO QUE NO SE BORRA
-- ------------------
-- Las ventas a crédito, las cuentas por cobrar, los cobros y sus aplicaciones del
-- piloto se quedan en la base, sin pantallas: es historia, y se decidió
-- conservarla. Las columnas de crédito de `clientes` también se quedan; nada las
-- vuelve a leer para decidir una venta.
--
-- Una venta a crédito que llegue tarde de un teléfono sin actualizar se registra
-- (§0.1: la mercancía ya salió) SIN cuenta por cobrar y marcada para revisión;
-- por eso `tipo` conserva 'credito' y la forma de pago es nula solo ahí.
-- =============================================================================

ALTER TABLE ventas
    ADD COLUMN forma_pago            text,
    ADD COLUMN referencia_pago       text,
    ADD COLUMN pago_estado           text NOT NULL DEFAULT 'confirmado',
    ADD COLUMN pago_resuelto_en      timestamptz,
    ADD COLUMN pago_resuelto_por     uuid REFERENCES usuarios(id),
    ADD COLUMN pago_resolucion_nota  text;

-- Lo de contado que ya existía se cobró en efectivo: era la única forma que el
-- teléfono sabía registrar en una venta.
UPDATE ventas SET forma_pago = 'efectivo' WHERE tipo = 'contado';

-- Y de aquí en adelante, lo que no diga otra cosa es efectivo. El valor por
-- omisión llega DESPUÉS de la actualización a propósito: así las ventas a
-- crédito del piloto se quedan sin forma de pago y ningún arqueo las cuenta.
ALTER TABLE ventas ALTER COLUMN forma_pago SET DEFAULT 'efectivo';

ALTER TABLE ventas ADD CONSTRAINT venta_forma_pago_check
    CHECK (forma_pago IN ('efectivo', 'transferencia'));

-- De contado sin forma de pago no existe: el arqueo no sabría si contarla.
ALTER TABLE ventas ADD CONSTRAINT venta_contado_con_forma_de_pago
    CHECK (tipo <> 'contado' OR forma_pago IS NOT NULL);

ALTER TABLE ventas ADD CONSTRAINT venta_pago_estado_check
    CHECK (pago_estado IN ('confirmado', 'por_confirmar', 'rechazado'));

-- El efectivo no se confirma contra un banco: está en la mano.
ALTER TABLE ventas ADD CONSTRAINT venta_efectivo_no_espera_banco
    CHECK (forma_pago IS DISTINCT FROM 'efectivo' OR pago_estado = 'confirmado');

-- «No llegó» es acusar: va con nombre y con motivo, o no va.
ALTER TABLE ventas ADD CONSTRAINT venta_pago_rechazado_con_motivo
    CHECK (pago_estado <> 'rechazado'
           OR (pago_resuelto_por IS NOT NULL
               AND length(btrim(COALESCE(pago_resolucion_nota, ''))) > 0));

CREATE INDEX idx_ventas_pago_por_confirmar
    ON ventas (fecha_servidor) WHERE pago_estado = 'por_confirmar';

COMMENT ON COLUMN ventas.forma_pago IS
    'Cómo pagó el cliente al recibir: efectivo o transferencia. Nula solo en las '
    'ventas a crédito del piloto (historia).';
COMMENT ON COLUMN ventas.pago_estado IS
    'confirmado (efectivo, o transferencia vista en el banco), por_confirmar '
    '(transferencia que la oficina no ha visto) o rechazado (no llegó).';

-- -----------------------------------------------------------------------------
-- El tablero: el dinero del día por forma de pago, y sin cartera
-- -----------------------------------------------------------------------------
-- `tablero_dia` es un CACHÉ que el worker recalcula desde las ventas; no es
-- historia. Sus columnas de crédito y de cobranza se cambian por lo que importa
-- ahora: cuánto se vendió en efectivo (lo que se entrega en el corte) y cuánto
-- por transferencia (lo que se confirma contra el banco).
ALTER TABLE tablero_dia
    ADD COLUMN venta_efectivo       numeric(14,2) NOT NULL DEFAULT 0,
    ADD COLUMN venta_transferencia  numeric(14,2) NOT NULL DEFAULT 0;
ALTER TABLE tablero_dia
    DROP COLUMN venta_contado,
    DROP COLUMN venta_credito,
    DROP COLUMN cobrado_total,
    DROP COLUMN cobrado_efectivo;

-- Se vacía para que el worker lo vuelva a calcular completo con las columnas
-- nuevas: un renglón viejo traería ceros en efectivo y transferencia.
DELETE FROM tablero_dia;

-- La cartera era un saldo de cuentas por cobrar. Ya no hay.
DROP TABLE tablero_cartera;

-- -----------------------------------------------------------------------------
-- El motivo de no-venta «Excedió su límite de crédito» ya no puede pasar
-- -----------------------------------------------------------------------------
-- Se desactiva, no se borra: hay visitas del piloto que lo usaron, y el
-- disparador del catálogo le avisa al teléfono para que deje de ofrecerlo.
UPDATE motivos_no_drop SET activo = false WHERE codigo = 'SIN_CREDITO';

-- -----------------------------------------------------------------------------
-- Una transferencia que no llegó se le puede cargar al vendedor
-- -----------------------------------------------------------------------------
-- Es la fuga que la migración 0038 cerró para los cobros: el vendedor cobra en
-- efectivo, lo captura como «transferencia» y la caja le cuadra. Sin crédito no
-- hay a quién más dejarle la deuda: si el dinero no está en el banco, la oficina
-- decide si lo carga a la cuenta del vendedor, ligado a la venta y una sola vez.
ALTER TABLE cuenta_vendedor ADD COLUMN venta_id uuid REFERENCES ventas(id);

ALTER TABLE cuenta_vendedor DROP CONSTRAINT cuenta_vendedor_origen_cuadra;
ALTER TABLE cuenta_vendedor ADD CONSTRAINT cuenta_vendedor_origen_cuadra CHECK (
    (tipo = 'cargo' AND origen IN ('faltante_mercancia', 'merma_atribuible',
                                   'faltante_efectivo', 'cobro_no_entregado',
                                   'transferencia_no_llego', 'cargo_manual'))
 OR (tipo = 'abono' AND origen IN ('descuento_nomina', 'pago'))
 OR (tipo = 'condonacion' AND origen = 'condonacion')
);

ALTER TABLE cuenta_vendedor DROP CONSTRAINT cuenta_vendedor_con_respaldo;
ALTER TABLE cuenta_vendedor ADD CONSTRAINT cuenta_vendedor_con_respaldo CHECK (
    CASE
      WHEN origen IN ('faltante_mercancia', 'merma_atribuible', 'faltante_efectivo')
        THEN liquidacion_id IS NOT NULL
      WHEN origen = 'cobro_no_entregado'
        THEN cobro_id IS NOT NULL AND registrado_por IS NOT NULL
      WHEN origen = 'transferencia_no_llego'
        THEN venta_id IS NOT NULL AND registrado_por IS NOT NULL
      ELSE registrado_por IS NOT NULL
    END
);

CREATE UNIQUE INDEX uq_cuenta_vendedor_por_venta
    ON cuenta_vendedor (venta_id) WHERE venta_id IS NOT NULL;
