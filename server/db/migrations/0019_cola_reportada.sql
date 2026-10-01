-- ---------------------------------------------------------------------------
-- 0019 — El teléfono reporta cuántas operaciones le quedan
-- ---------------------------------------------------------------------------
-- `liquidaciones.sync_completa` existe desde la migración 0004 y hasta hoy se
-- escribía en `true` porque una persona marcó una casilla. Era lo único honesto
-- que se podía hacer: el servidor no tiene forma de saber cuántos sobres le
-- quedan en la bandeja al teléfono. **Solo el teléfono lo sabe.**
--
-- El problema de esa casilla no es que exista, es que el dato que dejaba era
-- indistinguible de un hecho. Al auditar un cierre con sobrante, "sync_completa
-- = true" parecía decir "el equipo estaba al día" cuando en realidad decía
-- "alguien dijo que sí".
--
-- Ahora el push lo trae: el dispositivo manda cuántos sobres quedan en su cola
-- DESPUÉS del lote que está entregando, y aquí se guarda con su hora.
--
-- ---------------------------------------------------------------------------
-- POR QUÉ NULL Y 0 SON DISTINTOS
-- ---------------------------------------------------------------------------
-- `NULL` = este equipo nunca lo ha reportado (app vieja, o nunca sincronizó).
-- `0`    = el equipo dijo que no le queda nada.
--
-- La diferencia decide si el cierre puede descansar en un dato o tiene que
-- seguir pidiendo la confirmación de una persona. Un DEFAULT 0 habría borrado
-- esa distinción y habría hecho que cada equipo con app vieja pareciera estar al
-- día desde el primer día.
--
-- `cola_reportada_en` es igual de necesario: un cero de hace tres días no dice
-- nada sobre hoy (§0.3 — "tiempo real" es el tiempo real de lo que ya sincronizó).
-- ---------------------------------------------------------------------------

ALTER TABLE dispositivos
    ADD COLUMN IF NOT EXISTS cola_pendiente    integer
        CHECK (cola_pendiente IS NULL OR cola_pendiente >= 0),
    ADD COLUMN IF NOT EXISTS cola_reportada_en timestamptz;

COMMENT ON COLUMN dispositivos.cola_pendiente IS
    'Sobres que el dispositivo dijo que le quedaban tras su último push. NULL = '
    'nunca lo reportó, que no es lo mismo que cero.';
COMMENT ON COLUMN dispositivos.cola_reportada_en IS
    'Cuándo lo dijo. Un cero viejo no dice nada sobre hoy.';
