-- ---------------------------------------------------------------------------
-- 0018 — Los motivos de revisión de mermas y no-drops
-- ---------------------------------------------------------------------------
-- `ventas` y `cobros` ya guardan POR QUÉ quedaron marcados (`revision_motivos`),
-- y la oficina los lee de ahí. `mermas` y `no_drops` nacieron solo con la
-- bandera `requiere_revision`, que dice "revísalo" sin decir qué revisar: frente
-- a cincuenta mermas marcadas, el supervisor tendría que abrir las cincuenta
-- para descubrir que una no tenía existencia en el camión y las demás son otra
-- cosa.
--
-- Se agrega con el mismo tipo y la misma omisión que en ventas y cobros para que
-- el panel pueda leer las cuatro tablas con el mismo código.
-- ---------------------------------------------------------------------------

ALTER TABLE mermas   ADD COLUMN IF NOT EXISTS revision_motivos text[] NOT NULL DEFAULT '{}';
ALTER TABLE no_drops ADD COLUMN IF NOT EXISTS revision_motivos text[] NOT NULL DEFAULT '{}';
