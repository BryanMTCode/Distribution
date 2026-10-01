-- ===========================================================================
-- 0023 · Fase 9 — Borrado remoto del dispositivo
-- ===========================================================================
-- Revocar un equipo y borrarlo son dos cosas distintas, y mezclarlas cuesta
-- dinero real.
--
-- `estado = 'revocado'` ya existía desde la 0001: mata los tokens del equipo en
-- la siguiente petición. Eso protege los datos del SERVIDOR. Lo que no hace es
-- quitar la copia que el teléfono lleva dentro —la cartera de su ruta, los
-- precios, las ventas del día— que es justo lo que importa cuando el equipo se
-- perdió o la persona dejó la empresa.
--
-- ---------------------------------------------------------------------------
-- LA REGLA QUE GOBIERNA TODO ESTE ARCHIVO
-- ---------------------------------------------------------------------------
--     NUNCA SE BORRA LO QUE NO SE HA ENTREGADO.
--
-- Un borrado inmediato parece lo más seguro y es la decisión más costosa que se
-- podría tomar aquí. La razón es que el motivo real de un borrado casi nunca es
-- un robo:
--
--   · el vendedor renunció y hay que recuperar el equipo;
--   · se cambió de teléfono;
--   · el equipo se extravió y aún no aparece.
--
-- En los tres casos el teléfono puede traer dentro un día de ventas sin
-- sincronizar. Borrarlas es perder dinero cobrado, sin registro de a quién se
-- le vendió ni cuánto se le cobró, y sin forma de reconstruirlo.
--
-- Y en el caso que sí es un robo, borrar rápido no gana nada: la base local está
-- cifrada con SQLCipher y su llave vive en el Keystore, detrás del PIN. Quien se
-- lleva el teléfono no puede leer nada sin el PIN del vendedor.
--
-- Así que el flujo es: ORDENAR → DRENAR → BORRAR → CONFIRMAR.
--
--   1. La oficina ordena el borrado. El equipo pasa a `suspendido`.
--   2. `suspendido` puede HACER PUSH y no puede hacer pull: entrega lo que
--      trae y no recibe nada nuevo. Es la única razón de que ese estado exista
--      como algo más que una etiqueta.
--   3. Cuando el teléfono termina de entregar, borra su base y su credencial, y
--      lo CONFIRMA.
--   4. El servidor marca `revocado` y guarda cuándo. Ahí termina.
--
-- Si el teléfono no logra entregar —sin señal, apagado— no borra nada: queda
-- bloqueado mostrando cuántas operaciones le faltan por subir. Un equipo
-- bloqueado con datos dentro es recuperable; uno borrado, no.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ COLUMNAS Y NO UN ESTADO MÁS
-- ---------------------------------------------------------------------------
-- Se pensó en un `estado = 'por_borrar'`. Se descartó: la orden de borrado y el
-- estado del equipo son dos ejes independientes —se puede ordenar el borrado de
-- un equipo suspendido o de uno activo, y cancelar la orden sin cambiar el
-- estado— y meterlos en una sola columna obliga a inventar combinaciones.
-- ===========================================================================

ALTER TABLE dispositivos
    -- Cuándo se ordenó. NULL = sin orden.
    ADD COLUMN IF NOT EXISTS borrado_ordenado_en     timestamptz,
    ADD COLUMN IF NOT EXISTS borrado_ordenado_por    uuid REFERENCES usuarios(id),
    -- El motivo es OBLIGATORIO cuando hay orden (ver el CHECK de abajo). Un
    -- borrado sin motivo es una decisión que nadie puede revisar después, y
    -- ésta destruye datos.
    ADD COLUMN IF NOT EXISTS borrado_motivo          text,
    -- Cuándo el teléfono confirmó que ya borró. Es lo único que prueba que el
    -- borrado OCURRIÓ: la orden sola solo prueba que alguien la pidió.
    ADD COLUMN IF NOT EXISTS borrado_confirmado_en   timestamptz,
    -- Cuántos sobres le quedaban al confirmar. Debe ser 0 — el teléfono no
    -- borra con cola pendiente— y se guarda para poder demostrarlo después.
    -- Si algún día apareciera un número distinto de 0, es que una versión del
    -- cliente se saltó la regla, y esta columna es la única forma de notarlo.
    ADD COLUMN IF NOT EXISTS borrado_cola_al_confirmar integer;

ALTER TABLE dispositivos
    DROP CONSTRAINT IF EXISTS borrado_con_motivo;
ALTER TABLE dispositivos
    ADD CONSTRAINT borrado_con_motivo
    CHECK (borrado_ordenado_en IS NULL OR borrado_motivo IS NOT NULL);

ALTER TABLE dispositivos
    DROP CONSTRAINT IF EXISTS borrado_confirmado_tras_orden;
ALTER TABLE dispositivos
    -- No se puede confirmar un borrado que nadie ordenó. Sin esto, un cliente
    -- con un bug podría marcar como borrado un equipo que sigue en la calle
    -- trabajando, y la oficina lo daría por recuperado.
    ADD CONSTRAINT borrado_confirmado_tras_orden
    CHECK (borrado_confirmado_en IS NULL OR borrado_ordenado_en IS NOT NULL);

-- Los equipos con orden pendiente de confirmar. Es la lista que la oficina
-- tiene que mirar: una orden que llevas una semana sin confirmar significa que
-- el teléfono no se ha conectado, y eso es una decisión que tomar —ir por él—
-- no un dato que esperar.
CREATE INDEX IF NOT EXISTS idx_dispositivos_borrado_pendiente
    ON dispositivos (borrado_ordenado_en)
    WHERE borrado_ordenado_en IS NOT NULL AND borrado_confirmado_en IS NULL;

COMMENT ON COLUMN dispositivos.borrado_ordenado_en IS
    'Orden de borrado remoto. El teléfono entrega su cola ANTES de borrar: '
    'nunca se borra lo que no se ha entregado. Ver migración 0023.';
COMMENT ON COLUMN dispositivos.borrado_confirmado_en IS
    'Cuándo el propio teléfono confirmó haber borrado. Es lo único que prueba '
    'que el borrado ocurrió; la orden sola solo prueba que se pidió.';
