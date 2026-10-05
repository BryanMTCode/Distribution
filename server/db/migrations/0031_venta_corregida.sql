-- =============================================================================
-- 0031 · La oficina puede cancelar y corregir una venta, y el teléfono se entera
-- =============================================================================
-- Hasta aquí una venta recibida era inmutable desde el panel: se podía ver y
-- marcar para revisión, no corregir. Eso dejaba un hueco operativo real —el
-- vendedor teclea 20 cajas donde eran 2, el cliente devuelve todo en el momento,
-- se factura al cliente equivocado— y la única salida era un ajuste de inventario
-- que no explicaba nada y dejaba la venta mintiendo en el reporte del día.
--
-- `ventas_cancelaciones` existe desde la migración 0005, con su `motivo` y su
-- `reingresa_stock`, y nunca se conectó. Aquí se conecta, y se agrega lo que le
-- faltaba: la huella de una CORRECCIÓN, que no es lo mismo que una cancelación.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- LO QUE NO CAMBIA, Y HAY QUE DECIRLO
-- ─────────────────────────────────────────────────────────────────────────────
-- **El papel que el cliente tiene en la mano no se puede corregir.** La remisión
-- ya salió impresa con su folio y sus cantidades. Por eso una corrección deja su
-- rastro visible (`corregida_en`, `corregida_por`, `correccion_motivo`): quien
-- vea esa venta después tiene que poder saber que el sistema y el papel no dicen
-- lo mismo, y por qué. Un sistema que permite corregir sin dejar huella no es
-- más flexible, es menos auditable.
--
-- **Y el precio sigue sin tocarse.** Corregir una cantidad es decir «se
-- entregaron 2, no 20». Corregir un precio sería otorgar un descuento después del
-- hecho, desde la oficina, sobre un papel ya impreso — y el descuento en la calle
-- no existe (ADR 0002 §7). La pantalla no tiene campo de precio, igual que la del
-- vendedor.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. La huella de la corrección
-- -----------------------------------------------------------------------------
ALTER TABLE ventas
    ADD COLUMN IF NOT EXISTS corregida_en      timestamptz,
    ADD COLUMN IF NOT EXISTS corregida_por     uuid REFERENCES usuarios(id),
    ADD COLUMN IF NOT EXISTS correccion_motivo text;

COMMENT ON COLUMN ventas.correccion_motivo IS
    'Por qué la oficina corrigió las cantidades. Obligatorio al corregir: una '
    'venta que no coincide con su papel impreso tiene que decir por qué.';

-- Una venta se cancela UNA vez. Sin esto, un doble clic escribe dos documentos
-- de cancelación y devuelve la mercancía al camión dos veces.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cancelacion_venta
    ON ventas_cancelaciones(venta_id);

-- -----------------------------------------------------------------------------
-- 2. El delta de la venta: solo cuando la OFICINA la cambia
-- -----------------------------------------------------------------------------
-- El disparador es AFTER UPDATE y con una condición estrecha, por dos razones
-- distintas:
--
--   · **No se publica el INSERT.** Una venta nace en el teléfono y llega por
--     `sync/push`. Devolvérsela sería mandarle de vuelta lo que acaba de
--     escribir: ancho de banda gastado en un eco, y una oportunidad de aplicar
--     mal algo que ya estaba bien.
--
--   · **No se publica cualquier UPDATE.** El manejador de sincronización toca la
--     venta recién insertada para marcarle `requiere_revision`, y eso no le dice
--     nada al teléfono. Solo viaja lo que el vendedor necesita ver: que su venta
--     se canceló, que su importe cambió, o que la oficina la corrigió.
--
-- El payload lleva las partidas, como el de la carga: el teléfono las compara con
-- las suyas y devuelve al camión la diferencia. Mandar la diferencia ya calculada
-- sería más chico y más frágil —exigiría que el teléfono la aplicara exactamente
-- una vez—, y comparar es idempotente por construcción: aplicar dos veces el
-- mismo delta da el mismo saldo.
CREATE OR REPLACE FUNCTION fn_registrar_cambio_venta() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_registro jsonb;
    v_partidas jsonb;
BEGIN
    -- La geografía generada se va: el teléfono ya tiene lat y lng, y el
    -- hexadecimal de PostGIS solo engordaría el delta.
    v_registro := to_jsonb(NEW) - 'ubicacion';

    SELECT COALESCE(
             jsonb_agg(
               jsonb_build_object(
                 'id',              p.id,
                 'linea',           p.linea,
                 'producto_id',     p.producto_id,
                 'unidad_codigo',   p.unidad_codigo,
                 -- Cantidades e importes como TEXTO con su escala
                 -- (contracts/README.md §1.4): así `Cantidad` y `Dinero` las
                 -- consumen sin que ningún `double` toque el número.
                 'factor_unidad',   p.factor_unidad::text,
                 'cantidad',        p.cantidad::text,
                 'cantidad_base',   p.cantidad_base::text,
                 'precio_unitario', p.precio_unitario::text,
                 'tasa_iva',        p.tasa_iva::text,
                 'importe',         p.importe::text
               )
               ORDER BY p.linea
             ),
             '[]'::jsonb
           )
      INTO v_partidas
      FROM venta_partidas p
     WHERE p.venta_id = NEW.id;

    v_registro := v_registro || jsonb_build_object(
        'partidas', v_partidas,
        -- El motivo viaja con la venta para que el vendedor lo LEA en su
        -- teléfono. Que su venta cambie sin decirle por qué es la forma más
        -- rápida de que deje de confiar en el sistema.
        'cancelacion_motivo', (
            SELECT c.motivo FROM ventas_cancelaciones c WHERE c.venta_id = NEW.id
        )
    );

    INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, vendedor_id, payload)
    VALUES ('venta', NEW.id, 'upsert', NEW.ruta_id, NEW.vendedor_id, v_registro);
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_registrar_cambio_venta() IS
    'Publica una venta al teléfono del vendedor cuando la OFICINA la cambia: '
    'cancelada, corregida o con otro importe. El INSERT no se publica porque la '
    'venta nace en el teléfono.';

DROP TRIGGER IF EXISTS trg_cambio_venta ON ventas;
CREATE TRIGGER trg_cambio_venta
    AFTER UPDATE ON ventas
    FOR EACH ROW
    WHEN (OLD.estado        IS DISTINCT FROM NEW.estado
       OR OLD.total         IS DISTINCT FROM NEW.total
       OR OLD.corregida_en  IS DISTINCT FROM NEW.corregida_en)
    EXECUTE FUNCTION fn_registrar_cambio_venta();

-- -----------------------------------------------------------------------------
-- 3. Gerencia deja de ser de solo lectura
-- -----------------------------------------------------------------------------
-- La migración 0009 dice, con estas palabras: «Gerencia es de SOLO LECTURA sobre
-- la operación. Monitorea, no opera.» La dirección lo revirtió en octubre de 2026:
-- quiere que gerencia pueda corregir lo que ve, sin pedirle a nadie más.
--
-- Los tres permisos ya existían en el catálogo —solo no estaban concedidos—, así
-- que esto es un cambio de política, no de modelo:
--
--   ventas.cancelar        cancelar y corregir el documento de una venta
--   inventario.ajustar     ajustes autorizados de inventario, camión incluido
--   catalogo.administrar   editar y dar de baja productos
--
-- `inventario.liquidar` NO se concede: cerrar el día firma un arqueo con el nombre
-- de quien lo cierra, y nadie pidió mover eso.
--
-- Corregir una venta usa el mismo permiso que cancelarla, a propósito. Son el
-- mismo acto sobre el mismo documento —cambiarlo después de impreso—, y separarlos
-- daría la impresión de que uno es más leve que el otro. Lo que los distingue no
-- es el permiso: es el motivo, que en los dos casos es obligatorio.
INSERT INTO roles_permisos (rol_codigo, permiso_codigo) VALUES
 ('gerente', 'ventas.cancelar'),
 ('gerente', 'inventario.ajustar'),
 ('gerente', 'catalogo.administrar')
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ NO SE PUSO
-- -----------------------------------------------------------------------------
-- **Un CHECK que impida cancelar una venta de un día ya liquidado.** Se valida en
-- el panel, donde se le puede explicar a la persona qué firmó y qué tendría que
-- reabrir. En la base sería un error de restricción sin contexto sobre la única
-- pantalla desde la que se puede cancelar.
--
-- **Borrar la venta.** Nunca. El folio está impreso en un papel que el cliente
-- tiene, `movimientos_inventario` es append-only, y una venta que desaparece deja
-- un hueco en la numeración que nadie puede explicar tres meses después. Se
-- cancela, que es un estado, no una ausencia.
