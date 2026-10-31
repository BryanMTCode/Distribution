-- =============================================================================
-- 0075 · El producto malo que el vendedor tiene que regresar a la bodega
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §196): «si Federico tiene 4
-- malas, quiero llevar control de esas para que las regrese al almacén».
--
-- En un cambio físico (§54) el vendedor le da a la tienda producto bueno y se
-- lleva el malo. El bueno sale de su camión con documento; el malo se quedaba
-- sin nombre. Desde ahora queda POR REGRESAR a nombre del vendedor hasta que la
-- oficina lo recibe en la bodega o se lo carga a su cuenta.
--
-- Lo pendiente NO se guarda: se calcula de los cambios físicos confirmados menos
-- lo resuelto aquí. Así un cambio eliminado deja de contar solo, y los cambios
-- de antes de esta migración también cuentan.
-- =============================================================================

-- El almacén de la bodega donde queda lo que entregaron, para después tirarlo o
-- regresárselo al proveedor con una salida. Es de tipo 'merma' —no vale en lo
-- que vale la empresa—, pero NO es «el almacén de merma» que reciben las mermas
-- del teléfono y las de bodega: a este solo entra lo que alguien recibió.
ALTER TABLE almacenes ADD COLUMN IF NOT EXISTS producto_malo boolean NOT NULL DEFAULT false;

-- Uno solo.
CREATE UNIQUE INDEX IF NOT EXISTS uq_almacenes_producto_malo
    ON almacenes (producto_malo) WHERE producto_malo;

COMMENT ON COLUMN almacenes.producto_malo IS
    'El almacén «Producto malo» de la bodega: lo que los vendedores entregaron de '
    'sus cambios físicos (ADR 0002 §196). No recibe las mermas.';

-- Lo que se resolvió de lo pendiente: recibido en la bodega, o cargado a su
-- cuenta. Lo recibido apunta al ajuste que lo metió al almacén: si ese ajuste se
-- elimina en Operaciones, vuelve a quedar pendiente.
CREATE TABLE IF NOT EXISTS producto_malo_resuelto (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vendedor_id     uuid NOT NULL REFERENCES usuarios(id),
    producto_id     uuid NOT NULL REFERENCES productos(id),
    cantidad        numeric(14,3) NOT NULL CHECK (cantidad > 0),
    como            text NOT NULL CHECK (como IN ('recibido', 'cargado')),
    ajuste_id       uuid REFERENCES ajustes_camion(id),
    cargo_id        uuid REFERENCES cuenta_vendedor(id),
    -- Cargado: lo que se le cobró de este artículo, a costo (§54).
    importe         numeric(14,2) CHECK (importe IS NULL OR importe >= 0),
    fecha_operativa date NOT NULL,
    registrado_por  uuid NOT NULL REFERENCES usuarios(id),
    registrado_en   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT producto_malo_con_respaldo CHECK (
        (como = 'recibido' AND ajuste_id IS NOT NULL)
     OR (como = 'cargado' AND cargo_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_producto_malo_resuelto
    ON producto_malo_resuelto (vendedor_id, producto_id);

COMMENT ON TABLE producto_malo_resuelto IS
    'Lo que se resolvió del producto malo por regresar de cada vendedor: recibido '
    'en la bodega o cargado a su cuenta (ADR 0002 §196).';
