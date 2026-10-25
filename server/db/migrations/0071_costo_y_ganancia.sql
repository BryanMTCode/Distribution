-- =============================================================================
-- 0071 · El costo de lo que se vende, y quién ve el costo y la ganancia
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §180): «en el dashboard quiero
-- que me aparezca el costo del inventario, así como la ganancia que se obtendrá
-- en cada artículo», y también «la ganancia de lo que ya se vendió». «Solo en el
-- dashboard; solo yo tengo acceso.»
--
-- El costo de cada artículo ya existe: `producto_costos` (0027), el promedio
-- ponderado de lo que se compró. Pero es el de HOY: la ganancia de una venta de
-- la semana pasada calculada con el costo de hoy cambia cada vez que entra una
-- compra. Así que cada renglón de venta guarda el costo que tenía su artículo al
-- venderse.
--
-- En tablas aparte, como `producto_costos`, y no como una columna de la venta:
-- el costo no sale de la oficina, y la venta viaja al teléfono.
-- =============================================================================

-- Solo el administrador, que tiene todos los permisos: no se le da a ningún rol.
INSERT INTO permisos (codigo, descripcion, modulo) VALUES
 ('costos.ver', 'Ver y capturar el costo de los artículos y la ganancia', 'compras')
ON CONFLICT (codigo) DO NOTHING;

CREATE TABLE IF NOT EXISTS costo_de_lo_vendido (
    partida_id      uuid PRIMARY KEY REFERENCES venta_partidas(id) ON DELETE CASCADE,
    -- Por unidad base, como `producto_costos.costo_promedio`.
    costo_unitario  numeric(14,4) NOT NULL CHECK (costo_unitario >= 0),
    registrado_en   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE costo_de_lo_vendido IS
    'El costo promedio que tenía el artículo cuando se vendió cada renglón de una '
    'venta de la ruta. Sin renglón: el artículo no tenía costo al venderse, y se '
    'usa el de hoy (ADR 0002 §180).';

CREATE TABLE IF NOT EXISTS costo_de_lo_vendido_mayoreo (
    partida_id      bigint PRIMARY KEY
                    REFERENCES venta_mayoreo_partidas(id) ON DELETE CASCADE,
    costo_unitario  numeric(14,4) NOT NULL CHECK (costo_unitario >= 0),
    registrado_en   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE costo_de_lo_vendido_mayoreo IS
    'Lo mismo que costo_de_lo_vendido, para las ventas de mayoreo de la oficina.';

-- Lo escribe la base, no la API ni el teléfono: el costo no se le pregunta a
-- nadie. SECURITY DEFINER para leer el costo y escribirlo con el rol que sea.
CREATE OR REPLACE FUNCTION fn_costo_de_lo_vendido() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF TG_TABLE_NAME = 'venta_partidas' THEN
        INSERT INTO costo_de_lo_vendido (partida_id, costo_unitario)
        SELECT NEW.id, c.costo_promedio
          FROM producto_costos c
         WHERE c.producto_id = NEW.producto_id
        ON CONFLICT (partida_id) DO NOTHING;
    ELSE
        INSERT INTO costo_de_lo_vendido_mayoreo (partida_id, costo_unitario)
        SELECT NEW.id, c.costo_promedio
          FROM producto_costos c
         WHERE c.producto_id = NEW.producto_id
        ON CONFLICT (partida_id) DO NOTHING;
    END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_costo_de_lo_vendido ON venta_partidas;
CREATE TRIGGER trg_costo_de_lo_vendido
    AFTER INSERT ON venta_partidas
    FOR EACH ROW EXECUTE FUNCTION fn_costo_de_lo_vendido();

DROP TRIGGER IF EXISTS trg_costo_de_lo_vendido ON venta_mayoreo_partidas;
CREATE TRIGGER trg_costo_de_lo_vendido
    AFTER INSERT ON venta_mayoreo_partidas
    FOR EACH ROW EXECUTE FUNCTION fn_costo_de_lo_vendido();
