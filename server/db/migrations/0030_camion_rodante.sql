-- =============================================================================
-- 0030 · El camión es un ALMACÉN RODANTE: no amanece en ceros
-- =============================================================================
-- Decisión de negocio, octubre 2026, de la dirección:
--
--   «Para nosotros, el camión funciona como un almacén rodante. La mercancía
--    que no se vende en el día se queda a dormir en el camión y se acumula con
--    la carga del día siguiente. No se le debe cobrar como faltante al
--    vendedor.»
--
-- El modelo anterior era el opuesto, y lo era en todas sus capas: la carga
-- confirmada era "el inventario completo del día", el cierre bajaba todo a la
-- bodega y escribía un ajuste para dejar el camión EXACTAMENTE en cero, y el
-- teléfono vaciaba `existencias_camion` al recibir la carga liquidada.
--
-- Con mercancía durmiendo arriba del camión, ese modelo le cobra al vendedor
-- todo lo que no vendió. Cada noche. Es mercancía fantasma: está en el camión,
-- se puede contar y tocar, y el sistema la declaraba perdida.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- LA ECUACIÓN, CON EL TÉRMINO QUE LE FALTABA
-- ─────────────────────────────────────────────────────────────────────────────
--     antes:  esperado = cargado − vendido − merma + devuelto
--     ahora:  esperado = INICIAL + cargado − vendido − merma + devuelto
--
--     diferencia = contado − esperado
--
-- `inicial` es el saldo con el que el camión amaneció: lo que quedó de los días
-- anteriores. Sin ese término, todo el saldo inicial aparecía como faltante.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- Y EL CONTEO DEJA DE SER UN RETORNO
-- ─────────────────────────────────────────────────────────────────────────────
-- `cant_retornada` significaba «lo que bajó a la bodega». Ya no baja nada: se
-- cuenta lo que se queda arriba. La columna se renombra a `cant_contada` porque
-- un nombre que miente sobre lo que guarda es el origen del siguiente error, y
-- este renglón es el que decide cuánto se le cobra a una persona.
--
-- Lo que el cierre escribe ahora es UN ajuste por producto, el de la diferencia,
-- que deja el camión exactamente en lo contado. Cuando el conteo cuadra —el caso
-- normal— no se escribe ningún movimiento: no pasó nada físico que registrar.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. El saldo inicial, y el conteo con su nombre
-- -----------------------------------------------------------------------------
ALTER TABLE liquidacion_detalle
    ADD COLUMN IF NOT EXISTS cant_inicial numeric(14,3) NOT NULL DEFAULT 0;

COMMENT ON COLUMN liquidacion_detalle.cant_inicial IS
    'Saldo con el que el camión amaneció: lo que quedó de días anteriores. '
    'Lo calcula el cierre desde la existencia del camión y los documentos del '
    'día, y absorbe cualquier ajuste de oficina: al vendedor se le cobra la '
    'diferencia entre el conteo y lo que el sistema tiene, no las correcciones '
    'de la oficina.';

-- La columna generada depende de `cant_retornada`, así que no se puede renombrar
-- debajo de ella. Se tira, se renombra y se vuelve a crear con la ecuación
-- nueva. El índice parcial cuelga de la columna generada y cae con ella.
DROP INDEX IF EXISTS idx_liquidacion_faltantes;
ALTER TABLE liquidacion_detalle DROP COLUMN diferencia;
ALTER TABLE liquidacion_detalle RENAME COLUMN cant_retornada TO cant_contada;

COMMENT ON COLUMN liquidacion_detalle.cant_contada IS
    'Lo que se contó FÍSICAMENTE arriba del camión al cerrar el día. No baja a '
    'la bodega: el camión es un almacén rodante y la mercancía se queda.';

-- Réplica textual de `app.domain.liquidacion.esperado_en_camion`. Si las dos
-- dejaran de coincidir, el vendedor y la oficina discutirían sobre dos números
-- distintos; hay una prueba que las compara renglón por renglón.
ALTER TABLE liquidacion_detalle
    ADD COLUMN diferencia numeric(14,3)
    GENERATED ALWAYS AS (
        cant_contada
        - (cant_inicial + cant_cargada - cant_vendida - cant_merma + cant_devuelta)
    ) STORED;

CREATE INDEX idx_liquidacion_faltantes
    ON liquidacion_detalle(liquidacion_id) WHERE diferencia <> 0;

-- -----------------------------------------------------------------------------
-- 2. El delta de la carga liquidada publica el AJUSTE, no el vaciado
-- -----------------------------------------------------------------------------
-- Antes, el teléfono trataba `estado = 'liquidada'` como «vacía el camión». Ya
-- no: el camión conserva su mercancía. Lo único que el teléfono tiene que
-- aprender del cierre es la CORRECCIÓN que la oficina escribió al comparar el
-- conteo físico contra el saldo del sistema.
--
-- Se publica la diferencia y no el conteo, y la distinción es la que importa:
-- la oficina puede liquidar lo de ayer a media mañana, con el camión ya en la
-- calle y con la carga de hoy encima. Un conteo de ayer aplicado como «el camión
-- tiene esto» borraría la carga de hoy y las ventas de la mañana. Una diferencia
-- se suma al saldo que haya, y sigue siendo correcta cuando llega tarde.
--
-- Solo los renglones con diferencia distinta de cero: el caso normal es que
-- cuadre, y publicar ceros sería ruido que el teléfono tendría que ignorar.
CREATE OR REPLACE FUNCTION fn_registrar_cambio_carga() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
    v_detalle  jsonb;
    v_ajustes  jsonb;
BEGIN
    -- Un borrador no sale de la oficina: mientras se arma tiene renglones que
    -- alguien puede corregir, y publicarlo haría que el vendedor viera —y
    -- pudiera vender— mercancía que la bodega no le ha entregado. No hace falta
    -- para que el teléfono se entere después: el paso a 'confirmada' es un
    -- UPDATE, y este mismo disparador lo publica entonces, ya completo.
    IF (v_registro ->> 'estado') = 'borrador' THEN
        RETURN NULL;
    END IF;

    IF TG_OP <> 'DELETE' THEN
        -- El detalle viaja CON el encabezado, en un solo delta: el teléfono
        -- necesita la carga COMPLETA o nada. Con deltas por renglón, una tanda
        -- cortada a la mitad dejaría el camión con cinco de los doce productos,
        -- y el vendedor descubriría el faltante frente al cliente.
        SELECT COALESCE(
                 jsonb_agg(
                   jsonb_build_object(
                     'producto_id', d.producto_id,
                     -- Cantidad como TEXTO con tres decimales
                     -- (contracts/README.md §1.4): `numeric(14,3)::text` siempre
                     -- rinde '240.000', y así `Cantidad.deTexto` la consume sin
                     -- que ningún `double` toque el número en el camino.
                     'cantidad',    d.cantidad::text,
                     'lote',        d.lote,
                     'caducidad',   d.caducidad
                   )
                   -- Orden estable: el mismo delta emitido dos veces tiene que
                   -- ser el mismo JSON, o el fixture del contrato cambia solo.
                   ORDER BY d.producto_id, COALESCE(d.lote, '')
                 ),
                 '[]'::jsonb
               )
          INTO v_detalle
          FROM carga_detalle d
         WHERE d.carga_id = (v_registro ->> 'id')::uuid;

        v_registro := v_registro || jsonb_build_object('detalle', v_detalle);

        -- El ajuste del cierre, solo cuando el cierre ya ocurrió.
        IF (v_registro ->> 'estado') = 'liquidada' THEN
            SELECT COALESCE(
                     jsonb_agg(
                       jsonb_build_object(
                         'producto_id', ld.producto_id,
                         'cantidad',    ld.diferencia::text
                       )
                       ORDER BY ld.producto_id
                     ),
                     '[]'::jsonb
                   )
              INTO v_ajustes
              FROM liquidacion_detalle ld
              JOIN liquidaciones l ON l.id = ld.liquidacion_id
             WHERE l.carga_id = (v_registro ->> 'id')::uuid
               AND ld.diferencia <> 0;

            v_registro := v_registro || jsonb_build_object('ajustes', v_ajustes);
        END IF;
    END IF;

    INSERT INTO change_log (entidad, entidad_id, operacion, ruta_id, vendedor_id, payload)
    VALUES (
        'carga',
        (v_registro ->> 'id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
        (v_registro ->> 'ruta_id')::uuid,
        (v_registro ->> 'vendedor_id')::uuid,
        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE v_registro END
    );
    RETURN NULL;
END;
$$;

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ NO SE PUSO
-- -----------------------------------------------------------------------------
-- **Un CHECK que impida contar más de lo que el sistema cree.** Sería al revés
-- de §0.1: el conteo es el hecho físico y el sistema es la creencia. Cuando no
-- coinciden, el que se corrige es el sistema.
--
-- **Borrar el ajuste que deja el camión en cero del historial.** Las
-- liquidaciones ya cerradas conservan sus movimientos tal como se escribieron.
-- Lo que cambió es la regla a partir de hoy, no lo que pasó.
