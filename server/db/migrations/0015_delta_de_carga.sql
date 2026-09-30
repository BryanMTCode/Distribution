-- =============================================================================
-- 0015 · El delta de carga: folio, y el detalle que le faltaba
-- =============================================================================
-- La migración 0010 dejó un disparador que publicaba la carga en `change_log`.
-- Era insuficiente por dos razones que solo se ven al intentar usarlo:
--
--   1. **El payload era solo el encabezado.** `to_jsonb(cargas)` trae folio,
--      vendedor, fecha y estado. NO trae qué se cargó. El teléfono recibía
--      "existe una carga" y nada con qué llenar `existencias_camion`: sabía que
--      le habían cargado el camión, y no qué.
--
--   2. **Publicaba los borradores.** Una carga en `borrador` es una lista que
--      alguien está armando en la oficina, con renglones que puede quitar. Si
--      llega al teléfono, el vendedor ve inventario que la bodega todavía no le
--      entregó, y puede venderlo.
--
-- Aquí se arreglan las dos, y se agrega la secuencia del folio.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- El folio de la carga
-- -----------------------------------------------------------------------------
-- Mismo razonamiento que el código de cliente (0014): una secuencia, no
-- `max(folio)+1`. Dos personas armando cargas al mismo tiempo para dos camiones
-- distintos es el caso normal a las seis de la mañana, no la excepción.
CREATE SEQUENCE IF NOT EXISTS seq_folio_carga AS bigint START WITH 1;

SELECT setval(
    'seq_folio_carga',
    (SELECT COALESCE(MAX(substring(folio FROM 4)::bigint), 0) + 1
       FROM cargas
      WHERE folio ~ '^CG-[0-9]+$'),
    false
);

COMMENT ON SEQUENCE seq_folio_carga IS
    'Consecutivo del folio de carga. Se consume al crear el borrador en el panel.';

-- -----------------------------------------------------------------------------
-- El disparador, otra vez
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_registrar_cambio_carga() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_registro jsonb := to_jsonb(COALESCE(NEW, OLD));
    v_detalle  jsonb;
BEGIN
    -- ------------------------------------------------------------------
    -- Un borrador no sale de la oficina.
    -- ------------------------------------------------------------------
    -- Mientras se arma, la carga tiene renglones que alguien puede corregir o
    -- quitar. Publicarla haría que el vendedor viera —y pudiera vender—
    -- mercancía que la bodega no le ha entregado.
    --
    -- Y no hace falta publicar el borrador para que el teléfono se entere
    -- después: el cambio a 'confirmada' es un UPDATE, y este mismo disparador
    -- lo publica entonces, con el detalle ya completo.
    IF (v_registro ->> 'estado') = 'borrador' THEN
        RETURN NULL;
    END IF;

    -- ------------------------------------------------------------------
    -- El detalle viaja CON el encabezado, en un solo delta.
    -- ------------------------------------------------------------------
    -- La alternativa sería una entidad `carga_detalle` con su propio delta por
    -- renglón. Se descartó: el teléfono necesita la carga COMPLETA o nada. Con
    -- deltas por renglón, una tanda cortada a la mitad dejaría el camión con
    -- cinco de los doce productos que trae, y el vendedor descubriría el
    -- faltante frente al cliente.
    --
    -- Se agrega al final del snapshot, no se reemplaza: el encabezado lleva el
    -- `estado` y la `version`, y el teléfono los necesita para saber si este
    -- delta sustituye lo que ya tenía.
    IF TG_OP <> 'DELETE' THEN
        SELECT COALESCE(
                 jsonb_agg(
                   jsonb_build_object(
                     'producto_id', d.producto_id,
                     -- CANTIDAD COMO TEXTO, con sus tres decimales.
                     --
                     -- `numeric(14,3)::text` siempre rinde '240.000': la escala
                     -- declarada se conserva. Es la regla del contrato
                     -- (contracts/README.md §1.4: los flotantes están
                     -- prohibidos y la cantidad va como string de 3 decimales),
                     -- y así `Cantidad.deTexto` del lado de Dart la consume sin
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
-- **Un disparador sobre `carga_detalle`.** Sería lo natural: si cambia un
-- renglón, republicar la carga. Se descartó porque los renglones se capturan
-- mientras la carga es borrador —cuando no se publica nada— y una carga
-- confirmada NO se edita: lo que ya salió de la bodega se corrige con un
-- traspaso o un ajuste, que son documentos con su propia huella. Un disparador
-- ahí solo serviría para republicar cargas que nadie debería estar editando.
--
-- **Un CHECK que impida confirmar sin renglones.** Una carga confirmada vacía es
-- un error de captura, no un dato inválido: puede ser el día que un vendedor
-- sale solo a cobrar. Se valida en el panel, donde se le puede explicar a la
-- persona; en la base sería un error de restricción sin contexto.
