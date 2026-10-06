-- =============================================================================
-- 0038 · Transferencias y cheques: por confirmar hasta que el banco lo diga
-- =============================================================================
-- Hasta aquí, un cobro por transferencia o cheque se aplicaba a las facturas del
-- cliente en cuanto sincronizaba, con la sola palabra del vendedor. Nadie
-- revisaba que el dinero estuviera en la cuenta. Un vendedor podía cobrar $5,000
-- en efectivo, capturarlos como «transferencia», y la caja le cuadraba —la
-- transferencia no entra al arqueo— mientras el cliente quedaba pagado.
--
-- Regla de la dirección (octubre 2026): **una transferencia sin confirmar NO
-- libera crédito**. El saldo del cliente se restaura hasta que la oficina
-- confirma que el dinero está en firme.
--
--   efectivo        nace 'confirmado' y se aplica al sincronizar, como siempre:
--                   el dinero está en la mano y el arqueo lo cuenta.
--   transferencia,  nacen 'por_confirmar' y NO se aplican: la deuda del cliente
--   cheque          sigue completa. La oficina los confirma contra el estado de
--                   cuenta (y entonces se aplican en FIFO) o los rechaza.
--
-- Un cheque que rebota DESPUÉS de confirmado también se rechaza: la aplicación
-- se revierte y las facturas se vuelven a abrir.
--
-- Lo que el teléfono necesita saber viaja en la cartera: `por_confirmar` es lo
-- que el cliente reportó pagado y la oficina todavía no ve. Sin ese número el
-- vendedor vería la deuda completa y le volvería a cobrar.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Los estados nuevos, y lo que cada uno exige
-- -----------------------------------------------------------------------------
ALTER TABLE cobros DROP CONSTRAINT cobros_estado_check;
ALTER TABLE cobros ADD CONSTRAINT cobros_estado_check
    CHECK (estado IN ('confirmado', 'cancelado', 'por_confirmar', 'rechazado'));

ALTER TABLE cobros
    ADD COLUMN resuelto_en     timestamptz,
    ADD COLUMN resuelto_por    uuid REFERENCES usuarios(id),
    ADD COLUMN resolucion_nota text;

COMMENT ON COLUMN cobros.resuelto_por IS
    'Quién confirmó o rechazó una transferencia o un cheque. Nulo en efectivo: '
    'ese no se confirma, se cuenta en el arqueo.';

-- El efectivo no se confirma contra un banco: está en la mano. Un cobro en
-- efectivo «por confirmar» sería dinero que el arqueo cobra y la cartera no
-- abona, y el vendedor lo pagaría dos veces.
ALTER TABLE cobros ADD CONSTRAINT cobro_efectivo_no_espera_banco
    CHECK (forma_pago <> 'efectivo' OR estado IN ('confirmado', 'cancelado'));

-- Rechazar es acusar: va con nombre y con motivo, o no va.
ALTER TABLE cobros ADD CONSTRAINT cobro_rechazo_con_motivo
    CHECK (estado <> 'rechazado'
           OR (resuelto_por IS NOT NULL AND resolucion_nota IS NOT NULL
               AND length(btrim(resolucion_nota)) > 0));

-- Mientras no se confirma, y después de rechazado, no puede estar abonado a
-- ninguna factura. Es la regla de la dirección escrita donde ningún camino la
-- puede saltar: si `importe_aplicado` fuera mayor que cero, el crédito ya se
-- habría liberado.
ALTER TABLE cobros ADD CONSTRAINT cobro_sin_aplicar_hasta_confirmar
    CHECK (estado NOT IN ('por_confirmar', 'rechazado')
           OR (importe_aplicado = 0 AND saldo_a_favor = 0));

CREATE INDEX idx_cobros_por_confirmar
    ON cobros (fecha_servidor) WHERE estado = 'por_confirmar';

-- -----------------------------------------------------------------------------
-- La cartera, con lo que está por confirmar
-- -----------------------------------------------------------------------------
-- La columna va AL FINAL: `CREATE OR REPLACE VIEW` solo admite agregar columnas
-- después de las que ya existen. El saldo NO la resta —esa es la regla—; se
-- informa aparte para que el vendedor sepa que no debe volver a cobrar.
CREATE OR REPLACE VIEW v_cartera_cliente AS
SELECT
    c.id                                        AS cliente_id,
    c.codigo,
    c.nombre_comercial,
    c.ruta_id,
    c.permite_credito,
    c.bloqueado,
    c.limite_credito,
    c.dias_credito,
    COALESCE(cxc.saldo, 0)::numeric(14,2)       AS saldo,
    GREATEST(c.limite_credito - COALESCE(cxc.saldo, 0), 0)::numeric(14,2) AS disponible,
    (c.bloqueado OR NOT c.permite_credito
        OR COALESCE(cxc.saldo, 0) >= c.limite_credito)  AS credito_agotado,
    COALESCE(cxc.facturas_abiertas, 0)          AS facturas_abiertas,
    COALESCE(cxc.vencidas, 0)                   AS facturas_vencidas,
    COALESCE(cxc.saldo_vencido, 0)::numeric(14,2) AS saldo_vencido,
    cxc.vencimiento_mas_antiguo,
    COALESCE(pc.importe, 0)::numeric(14,2)      AS por_confirmar
FROM clientes c
LEFT JOIN LATERAL (
    SELECT
        SUM(x.saldo)                                              AS saldo,
        COUNT(*) FILTER (WHERE x.estado <> 'liquidada')           AS facturas_abiertas,
        COUNT(*) FILTER (WHERE x.estado IN ('abierta','parcial')
                           AND x.fecha_vencimiento < CURRENT_DATE) AS vencidas,
        SUM(x.saldo) FILTER (WHERE x.estado IN ('abierta','parcial')
                           AND x.fecha_vencimiento < CURRENT_DATE) AS saldo_vencido,
        MIN(x.fecha_vencimiento) FILTER (WHERE x.estado IN ('abierta','parcial')) AS vencimiento_mas_antiguo
    FROM cuentas_por_cobrar x
    WHERE x.cliente_id = c.id AND x.estado <> 'liquidada'
) cxc ON true
LEFT JOIN LATERAL (
    SELECT SUM(k.importe) AS importe
      FROM cobros k
     WHERE k.cliente_id = c.id AND k.estado = 'por_confirmar'
) pc ON true
WHERE c.estatus <> 'baja';

-- -----------------------------------------------------------------------------
-- El teléfono se entera cuando nace, se confirma o se rechaza
-- -----------------------------------------------------------------------------
-- Confirmar ya publica la cartera por su lado —la aplicación FIFO toca
-- `cuentas_por_cobrar`—, pero rechazar uno por confirmar no toca ninguna factura,
-- y nacer tampoco. Sin estos disparadores el teléfono seguiría mostrando «por
-- confirmar» un pago que la oficina ya rechazó, y el vendedor no se lo cobraría.
--
-- Es la MISMA función de la cartera (ya SECURITY DEFINER desde la 0029): lee la
-- vista para `cliente_id`, que `cobros` también tiene.
CREATE TRIGGER trg_cartera_por_cobro_nuevo
    AFTER INSERT ON cobros
    FOR EACH ROW
    WHEN (NEW.estado = 'por_confirmar')
    EXECUTE FUNCTION fn_registrar_cambio_cartera();

CREATE TRIGGER trg_cartera_por_cobro_resuelto
    AFTER UPDATE OF estado ON cobros
    FOR EACH ROW
    WHEN (OLD.estado IS DISTINCT FROM NEW.estado)
    EXECUTE FUNCTION fn_registrar_cambio_cartera();

-- -----------------------------------------------------------------------------
-- Quién confirma
-- -----------------------------------------------------------------------------
-- Gerencia (y el administrador, que tiene todo). El supervisor no: es quien
-- vigila a los vendedores en la calle, y que la misma mano que cuida la ruta
-- dé por buena la transferencia de su vendedor es la separación de manos que
-- esta migración existe para crear.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('cobranza.confirmar', 'Confirmar o rechazar transferencias y cheques', 'cobranza')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente', 'cobranza.confirmar')
ON CONFLICT DO NOTHING;
