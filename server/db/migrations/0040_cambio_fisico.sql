-- =============================================================================
-- 0040 · El cambio físico: fresco por caducado, sin dinero de por medio
-- =============================================================================
-- En la calle el cliente enseña un producto caducado o dañado, y el vendedor se
-- lo cambia por uno fresco del camión. Pasa seguido, y no había cómo
-- registrarlo:
--
--   · como venta, no: el cliente no paga nada, y el arqueo esperaría dinero;
--   · como devolución, no: la devolución METE mercancía al camión, y aquí sale;
--   · sin registrarlo, el fresco sale del camión sin documento y el Corte del
--     día lo encuentra como faltante, que ahora se le carga al vendedor a costo.
--
-- Regla de la dirección (octubre 2026): el fresco sale del camión LEGALMENTE, el
-- malo regresa como merma, y al vendedor no se le descuadra el arqueo ni se le
-- exige un cobro.
--
-- Es un documento más de la tabla `mermas`, tipo 'cambio':
--
--   · SALE del camión, como la merma: lo que sale es el producto fresco. Lo que
--     el cliente entrega es el malo, que va al almacén de merma si la empresa
--     tiene uno — es la misma unidad del lado de la pérdida, así que el libro
--     mayor queda igual que en una merma.
--   · EXIGE cliente: un cambio sin a quién se le cambió es exactamente cómo se
--     escondería mercancía robada.
--   · NUNCA se carga al vendedor, aunque el motivo diga `afecta_vendedor`: el
--     producto se echó a perder en la tienda del cliente, no en su camión (ver
--     `app/infra/cuenta_vendedor.py`, que solo carga tipo 'merma').
--   · En el Corte del día cuenta en la columna «merma»: es mercancía que salió
--     del camión con documento, y la ecuación no necesita saber por qué.
-- =============================================================================

ALTER TABLE mermas DROP CONSTRAINT mermas_tipo_check;
ALTER TABLE mermas ADD CONSTRAINT mermas_tipo_check
    CHECK (tipo IN ('merma', 'devolucion_cliente', 'cambio'));

ALTER TABLE mermas DROP CONSTRAINT devolucion_requiere_cliente;
ALTER TABLE mermas ADD CONSTRAINT devolucion_requiere_cliente
    CHECK (tipo NOT IN ('devolucion_cliente', 'cambio') OR cliente_id IS NOT NULL);
