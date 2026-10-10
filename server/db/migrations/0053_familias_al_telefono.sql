-- =============================================================================
-- 0053 · Las familias de artículos llegan al teléfono
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §92): «agrupa también en la app
-- los artículos». El panel ya los agrupa por familia (§89); el teléfono no podía:
-- cada producto le llega con su `categoria_id` —el delta es la fila entera—, pero
-- el nombre y el orden de la familia no viajaban.
--
-- La familia viaja como su propia entidad de catálogo, `categoria`, global como
-- los productos. Así un cambio de nombre o de orden en el panel es UN delta, y no
-- el de cada artículo de la familia.
--
-- Las que ya existen se publican aquí una vez: sin eso, un teléfono que
-- sincroniza desde el cursor 0 recibiría los artículos con su `categoria_id` y
-- ninguna familia a la cual apuntar (la lección de la 0013).
-- =============================================================================

DROP TRIGGER IF EXISTS trg_cambio_categoria ON categorias;
CREATE TRIGGER trg_cambio_categoria
    AFTER INSERT OR UPDATE OR DELETE ON categorias
    FOR EACH ROW EXECUTE FUNCTION fn_registrar_cambio('categoria', 'id');

UPDATE categorias SET nombre = nombre;
