-- =============================================================================
-- 0076 · El producto malo se cambia por bueno en la bodega
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §197): «en la aplicación no
-- haya nada de devolución, mermas ni nada; los vendedores solo llevarán la
-- mercancía mala al almacén y se las cambiaremos por buena».
--
-- La app de los vendedores ya no registra mermas, devoluciones ni cambios. El
-- vendedor entrega lo malo en la bodega y se lleva lo mismo, bueno: su camión no
-- cambia. La oficina lo registra con un movimiento de la bodega al almacén
-- «Producto malo» (lo bueno sale, lo malo entra), y aquí queda a nombre de quién
-- lo trajo: 'cambiado'. No es lo pendiente de §196 —eso sale de los cambios que
-- registró el teléfono—: es lo que cada vendedor ha traído.
-- =============================================================================

ALTER TABLE producto_malo_resuelto DROP CONSTRAINT IF EXISTS producto_malo_resuelto_como_check;
ALTER TABLE producto_malo_resuelto
    ADD CONSTRAINT producto_malo_resuelto_como_check
    CHECK (como IN ('recibido', 'cargado', 'cambiado'));

ALTER TABLE producto_malo_resuelto DROP CONSTRAINT IF EXISTS producto_malo_con_respaldo;
ALTER TABLE producto_malo_resuelto
    ADD CONSTRAINT producto_malo_con_respaldo CHECK (
        (como IN ('recibido', 'cambiado') AND ajuste_id IS NOT NULL)
     OR (como = 'cargado' AND cargo_id IS NOT NULL)
    );

COMMENT ON TABLE producto_malo_resuelto IS
    'El producto malo a nombre de cada vendedor: lo recibido o cargado de sus '
    'cambios físicos (ADR 0002 §196) y lo que trajo a la bodega y se le cambió por '
    'bueno (§197).';
