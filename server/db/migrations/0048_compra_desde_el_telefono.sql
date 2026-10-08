-- =============================================================================
-- 0048 · La compra que el gerente recibe desde el teléfono, aunque no haya señal
-- =============================================================================
-- Retroalimentación del piloto (octubre 2026, ADR 0002 §83). «Agregar producto»
-- en el teléfono del gerente NO es dar de alta un artículo en el catálogo: es
-- recibir mercancía de un proveedor en la calle. Se captura sin señal, se manda
-- al tener señal con el id que le dio el teléfono —así reintentar no la suma
-- dos veces— y entra a la bodega principal como una entrada de compra.
--
-- El costo es opcional en ESA compra: el renglón que lo trae mueve el costo
-- promedio y entra a la cuenta por pagar del proveedor; el que no lo trae solo
-- suma inventario. Las compras capturadas en el panel siguen exigiéndolo.
-- =============================================================================

ALTER TABLE entradas
    ADD COLUMN IF NOT EXISTS costo_opcional boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN entradas.costo_opcional IS
    'Compra recibida desde el teléfono del gerente (§83): el renglón sin costo '
    'solo suma inventario; no mueve el promedio ni entra a la cuenta por pagar.';
