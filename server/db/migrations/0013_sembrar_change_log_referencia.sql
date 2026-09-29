-- =============================================================================
-- 0013 · Los datos de referencia que el dispositivo nunca recibió
-- =============================================================================
-- BUG DE ORDEN DE MIGRACIONES, encontrado al construir el catálogo del vendedor.
--
-- La migración 0009 siembra la lista de precios GENERAL. Los triggers que
-- alimentan `change_log` se crearon en la 0010, **después**. Resultado: esa fila
-- existe en `listas_precios` pero NO tiene renglón en `change_log`, así que un
-- dispositivo que sincroniza desde el cursor 0 jamás la recibe.
--
-- No se nota en las pruebas de sincronización —crean sus propias listas, que sí
-- disparan el trigger— y no se notaba en la app, porque hasta ahora el
-- dispositivo no guardaba listas de precios. Se nota en el primer teléfono real
-- del primer vendedor real, así:
--
--   1. El vendedor da de alta una tienda en la calle.
--   2. Ese cliente nace sin `lista_precios_id` (la asigna el servidor al
--      confirmarlo).
--   3. El catálogo cae a la lista por omisión... que el teléfono no tiene.
--   4. No hay precios. No le puede vender al cliente que acaba de registrar.
--
-- Que es exactamente lo contrario de para qué existe el alta en campo.
--
-- -----------------------------------------------------------------------------
-- POR QUÉ UN INSERT Y NO "QUE LO ARREGLE EL TRIGGER"
-- -----------------------------------------------------------------------------
-- Un `UPDATE listas_precios SET nombre = nombre` dispararía el trigger y
-- generaría el renglón, sí. Pero dejaría en el historial un cambio que nunca
-- ocurrió, y `change_log` es la bitácora de lo que le pasó al catálogo. Se
-- escribe el renglón directamente, con el mismo payload que produciría el
-- trigger (`to_jsonb` de la fila), y este comentario explica de dónde salió.
--
-- Idempotente: si ya hay un renglón para esa lista, no se duplica.
-- =============================================================================

INSERT INTO change_log (entidad, entidad_id, operacion, payload)
SELECT 'lista_precios', l.id, 'upsert', to_jsonb(l)
  FROM listas_precios l
 WHERE NOT EXISTS (
        SELECT 1 FROM change_log c
         WHERE c.entidad = 'lista_precios'
           AND c.entidad_id = l.id
       );

-- -----------------------------------------------------------------------------
-- La misma revisión, para lo demás que el dispositivo espeja
-- -----------------------------------------------------------------------------
-- `productos`, `producto_unidades`, `precios` y `clientes` NO se siembran en la
-- 0009: nacen por la API, con los triggers ya puestos, así que sus renglones de
-- `change_log` existen. Se deja esta consulta como comprobación para quien
-- agregue semillas en el futuro:
--
--   SELECT 'producto' AS entidad, count(*) FROM productos p
--    WHERE NOT EXISTS (SELECT 1 FROM change_log c
--                       WHERE c.entidad='producto' AND c.entidad_id=p.id)
--   UNION ALL
--   SELECT 'precio', count(*) FROM precios pr
--    WHERE NOT EXISTS (SELECT 1 FROM change_log c
--                       WHERE c.entidad='precio' AND c.entidad_id=pr.producto_id);
--
-- Si alguna vez devuelve algo distinto de cero, hay mercancía invisible para los
-- teléfonos. `server/tests/test_sync_pull.py` lo verifica automáticamente.
