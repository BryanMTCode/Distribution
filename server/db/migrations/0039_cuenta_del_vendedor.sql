-- =============================================================================
-- 0039 · La cuenta del vendedor: lo que debe, por qué, y cómo lo paga
-- =============================================================================
-- El Corte del día ya calculaba el faltante —«faltan 3 cajas», «faltan $420»—,
-- con el nombre del vendedor. Y ahí terminaba: no había dónde acumularlo de un día
-- a otro, ni cómo registrar que se descontó de la nómina, ni quién lo perdonó. En
-- un mes nadie sabía cuánto debía cada uno, y el faltante quedaba perdonado de
-- hecho, sin que nadie lo hubiera decidido.
--
-- Reglas de la dirección (octubre 2026):
--
--   · La mercancía se cobra a COSTO, no a precio de venta: se recupera la
--     pérdida real del inventario, no se gana margen con el error del empleado.
--     El costo es el promedio ponderado de `producto_costos` (ADR 0002 §41).
--
-- Qué se carga solo, al cerrar el Corte del día:
--
--   faltante_mercancia  lo contado quedó abajo de lo esperado, a costo
--   merma_atribuible    las mermas del día cuyo motivo dice `afecta_vendedor`
--                       (roto, dañado en el transporte, robo), a costo
--   faltante_efectivo   entregó menos efectivo del esperado
--
-- Qué se carga a mano, con nombre:
--
--   cobro_no_entregado  el cliente sí pagó (tiene su recibo) y el dinero no
--                       llegó: se le abona al cliente y se le carga al vendedor
--   cargo_manual        cualquier otro, con su concepto
--
-- Y cómo baja: `descuento_nomina` y `pago` (abonos), o `condonacion`, que es
-- perdonar y por eso exige motivo y quién.
--
-- Es un LIBRO: solo se agrega. Un cargo equivocado no se edita ni se borra —se
-- compensa con una condonación que dice por qué—, igual que el libro mayor del
-- inventario. Y no lleva disparador de change_log: trae costos, y el costo no
-- sale de la oficina (ADR 0002 §41).
-- =============================================================================

CREATE TABLE cuenta_vendedor (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vendedor_id     uuid NOT NULL REFERENCES usuarios(id),
    tipo            text NOT NULL CHECK (tipo IN ('cargo', 'abono', 'condonacion')),
    origen          text NOT NULL,
    importe         numeric(14,2) NOT NULL CHECK (importe > 0),
    fecha           date NOT NULL,
    concepto        text NOT NULL CHECK (length(btrim(concepto)) > 0),

    -- El desglose que justifica el importe: producto, cantidad y costo de cada
    -- renglón de un faltante. Es lo que se le enseña al vendedor cuando pregunta
    -- «¿de dónde salen estos $380?».
    detalle         jsonb,

    liquidacion_id  uuid REFERENCES liquidaciones(id),
    cobro_id        uuid REFERENCES cobros(id),
    registrado_por  uuid REFERENCES usuarios(id),
    registrado_en   timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT cuenta_vendedor_origen_cuadra CHECK (
        (tipo = 'cargo' AND origen IN ('faltante_mercancia', 'merma_atribuible',
                                       'faltante_efectivo', 'cobro_no_entregado',
                                       'cargo_manual'))
     OR (tipo = 'abono' AND origen IN ('descuento_nomina', 'pago'))
     OR (tipo = 'condonacion' AND origen = 'condonacion')
    ),

    -- Lo automático nace de un Corte del día; lo demás, de una persona.
    CONSTRAINT cuenta_vendedor_con_respaldo CHECK (
        CASE
          WHEN origen IN ('faltante_mercancia', 'merma_atribuible', 'faltante_efectivo')
            THEN liquidacion_id IS NOT NULL
          WHEN origen = 'cobro_no_entregado'
            THEN cobro_id IS NOT NULL AND registrado_por IS NOT NULL
          ELSE registrado_por IS NOT NULL
        END
    )
);

COMMENT ON TABLE cuenta_vendedor IS
    'Lo que cada vendedor debe a la empresa y cómo lo paga. Append-only, sin '
    'change_log: trae costos. Ver la migración 0039 y ADR 0002 §54.';

-- Un Corte del día carga cada concepto UNA vez, y un cobro se le carga al
-- vendedor una vez. Es lo que hace inofensivo un doble clic en «Cerrar».
CREATE UNIQUE INDEX uq_cuenta_vendedor_por_corte
    ON cuenta_vendedor (liquidacion_id, origen) WHERE liquidacion_id IS NOT NULL;
CREATE UNIQUE INDEX uq_cuenta_vendedor_por_cobro
    ON cuenta_vendedor (cobro_id) WHERE cobro_id IS NOT NULL;
CREATE INDEX idx_cuenta_vendedor ON cuenta_vendedor (vendedor_id, fecha DESC, registrado_en DESC);

-- Inmutable de verdad, con la misma función que el libro mayor (0004).
CREATE TRIGGER trg_cuenta_vendedor_inmutable
    BEFORE UPDATE OR DELETE ON cuenta_vendedor
    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_mutacion();

-- -----------------------------------------------------------------------------
-- El saldo de cada vendedor
-- -----------------------------------------------------------------------------
CREATE VIEW v_cuenta_vendedor AS
SELECT vendedor_id,
       COALESCE(sum(importe) FILTER (WHERE tipo = 'cargo'), 0)::numeric(14,2)       AS cargos,
       COALESCE(sum(importe) FILTER (WHERE tipo = 'abono'), 0)::numeric(14,2)       AS abonos,
       COALESCE(sum(importe) FILTER (WHERE tipo = 'condonacion'), 0)::numeric(14,2) AS condonado,
       (COALESCE(sum(importe) FILTER (WHERE tipo = 'cargo'), 0)
        - COALESCE(sum(importe) FILTER (WHERE tipo <> 'cargo'), 0))::numeric(14,2)  AS saldo,
       max(fecha) AS ultimo_movimiento
  FROM cuenta_vendedor
 GROUP BY vendedor_id;

-- -----------------------------------------------------------------------------
-- Saber que el arqueo se hizo
-- -----------------------------------------------------------------------------
-- `efectivo_entregado` nace en 0, así que un Corte cerrado sin arqueo parecía un
-- vendedor que no entregó NADA. Antes daba igual —nadie cobraba con ese número—;
-- ahora se carga a una persona. Con la hora del arqueo, el cierre sabe distinguir
-- «entregó $0» de «nadie contó».
ALTER TABLE liquidaciones ADD COLUMN arqueo_en timestamptz;

UPDATE liquidaciones SET arqueo_en = cerrada_en
 WHERE estado = 'cerrada' AND efectivo_entregado > 0;

-- -----------------------------------------------------------------------------
-- Quién ve y quién mueve
-- -----------------------------------------------------------------------------
-- Ver: supervisor y gerencia. Mover (abonos, condonar, cargos a mano): gerencia.
-- Perdonar dinero es una decisión de la dirección, no de quien vigila la ruta.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('vendedores.cuenta_ver',      'Consultar lo que debe cada vendedor',            'vendedores'),
 ('vendedores.cuenta_mover',    'Registrar abonos, condonar y cargar a un vendedor', 'vendedores')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('supervisor', 'vendedores.cuenta_ver'),
 ('gerente',    'vendedores.cuenta_ver'),
 ('gerente',    'vendedores.cuenta_mover')
ON CONFLICT DO NOTHING;
