-- =============================================================================
-- 0073 · La tarjeta de débito de la empresa, y lo que vale la empresa
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §190): «quiero en el dashboard
-- tener la cuenta del almacén, más los camiones, más la tarjeta de débito de la
-- empresa: las tres visiones distintas y el total. Y las compras poder
-- agregarlas de la tarjeta: a la tarjeta, la venta y las salidas.»
--
-- La tarjeta no es una tabla de saldos: es una cuenta que se calcula. Lo que ya
-- está en el sistema entra solo —el efectivo que entrega cada vendedor en su
-- corte, el mayoreo, lo que un vendedor paga de su cuenta— y sale solo —el pago
-- de una compra hecho con la tarjeta—. Aquí se guarda únicamente lo que no está
-- en ningún otro lado: con cuánto empezó la tarjeta, el dinero que sale por otra
-- cosa (gasolina, sueldos, un retiro), el que entra por otra cosa, y los ajustes
-- para que el sistema diga lo mismo que el banco.
--
-- Así, un corte o una venta de mayoreo que se elimina en Operaciones deja de
-- contar en la tarjeta sin que nadie lo tenga que deshacer aquí.
-- =============================================================================

-- Solo el administrador, que tiene todos los permisos: no se le da a ningún rol.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('tarjeta.ver', 'Ver lo que vale la empresa y la tarjeta, y registrar sus movimientos',
  'compras')
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS movimientos_tarjeta (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    --   inicial  · con cuánto empezó; desde ese momento cuenta lo automático
    --   entrada  · dinero que entra por otra cosa que una venta
    --   salida   · dinero que sale por otra cosa que una compra con la tarjeta
    --   ajuste   · la diferencia para que diga lo mismo que el banco
    tipo            text NOT NULL CHECK (tipo IN ('inicial', 'entrada', 'salida', 'ajuste')),
    -- Con su signo: lo que entra, en positivo; lo que sale, en negativo.
    importe         numeric(14,2) NOT NULL,
    fecha           date NOT NULL,
    concepto        text NOT NULL CHECK (length(btrim(concepto)) >= 3),
    -- Qué fue lo que salió (§192): un gasto de la empresa —que se resta de la
    -- ganancia— o un retiro del dueño, que no es gasto.
    categoria       text CHECK (categoria IN ('sueldos', 'gasolina', 'mantenimiento',
                                              'renta', 'otro_gasto', 'retiro')),
    registrado_por  uuid NOT NULL REFERENCES usuarios(id),
    registrado_en   timestamptz NOT NULL DEFAULT now(),

    -- Lo capturado por error se quita, pero se queda con quién y por qué.
    quitado_en      timestamptz,
    quitado_por     uuid REFERENCES usuarios(id),
    motivo_quitado  text,

    CONSTRAINT movimiento_tarjeta_con_signo CHECK (
        (tipo = 'entrada' AND importe > 0)
     OR (tipo = 'salida' AND importe < 0)
     OR (tipo = 'inicial' AND importe >= 0)
     OR (tipo = 'ajuste' AND importe <> 0)
    ),
    CONSTRAINT salida_con_categoria CHECK ((tipo = 'salida') = (categoria IS NOT NULL)),
    CONSTRAINT movimiento_tarjeta_quitado_con_motivo CHECK (
        quitado_en IS NULL
        OR (quitado_por IS NOT NULL AND length(btrim(coalesce(motivo_quitado, ''))) >= 3)
    )
);

-- Un solo inicio vivo: es el que dice desde cuándo cuenta lo automático.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tarjeta_un_inicio
    ON movimientos_tarjeta ((true)) WHERE tipo = 'inicial' AND quitado_en IS NULL;

CREATE INDEX IF NOT EXISTS idx_tarjeta_registrado
    ON movimientos_tarjeta (registrado_en DESC);

COMMENT ON TABLE movimientos_tarjeta IS
    'Lo que la tarjeta de la empresa movió y no está en otro lado: el saldo con que '
    'empezó, las entradas y salidas de dinero, y los ajustes. Los cortes, el mayoreo, '
    'los pagos de los vendedores y las compras pagadas con la tarjeta se suman solos '
    '(ADR 0002 §190).';

-- La compra se paga con la tarjeta de la empresa: una forma de pago más.
ALTER TABLE pagos_proveedor DROP CONSTRAINT IF EXISTS pagos_proveedor_forma_pago_check;
ALTER TABLE pagos_proveedor ADD CONSTRAINT pagos_proveedor_forma_pago_check
    CHECK (forma_pago IN ('efectivo', 'transferencia', 'cheque', 'tarjeta'));
