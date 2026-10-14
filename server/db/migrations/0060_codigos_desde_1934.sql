-- =============================================================================
-- 0060 · Las tiendas nuevas siguen en 1934
-- =============================================================================
-- Pedido de la dirección (octubre 2026, ADR 0002 §122): «que las tiendas nuevas
-- sigan en 1934». Las de los Excel de rutas llevan los códigos 100 a 1933
-- (§120); la siguiente que se confirme —o la de mayoreo que se dé de alta— lleva
-- 1934, y no «C00001» como hasta ahora. Las que ya tienen código «C…» lo
-- conservan: la ruta ya las conoce por ese número.
--
-- La secuencia arranca en 1934 aunque las tiendas del Excel todavía no se hayan
-- cargado, para no pisar su rango; y si ya hay un código numérico mayor, después
-- de ese.
-- =============================================================================

SELECT setval(
    'seq_codigo_cliente',
    GREATEST(
        1934,
        (SELECT COALESCE(MAX(codigo::bigint), 0) + 1
           FROM clientes
          WHERE codigo ~ '^[0-9]{1,15}$'),
        -- Lo que la secuencia ya iba a dar: nunca hacia atrás.
        (SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END
           FROM seq_codigo_cliente)
    ),
    false
);

COMMENT ON SEQUENCE seq_codigo_cliente IS
    'Consecutivo de clientes/codigo: 1934, 1935… (las tiendas de los Excel de rutas '
    'son 100–1933). Se consume al confirmar un prospecto o dar de alta un cliente de '
    'mayoreo. ADR 0002 §122.';
