-- =============================================================================
-- Prueba de humo: las 5 invariantes que sostienen el diseño offline
-- =============================================================================
-- Ejecutar sobre una BD con las migraciones 0001-0007 aplicadas:
--   psql -d dsd -v ON_ERROR_STOP=1 -f server/db/tests/smoke_invariantes.sql
--
-- Que el DDL compile no prueba nada. Esto prueba que el modelo REALMENTE
-- impide duplicar tickets y descuadrar inventarios.
-- =============================================================================

\set QUIET on
BEGIN;

-- ---------------------------------------------------------------- semilla ---
INSERT INTO roles(codigo, nombre) VALUES ('vendedor','Vendedor'),('admin','Administrador');
INSERT INTO sucursales(id, codigo, nombre)
     VALUES ('11111111-1111-1111-1111-111111111111','MATRIZ','Matriz');

INSERT INTO usuarios(id, codigo, nombre, password_hash, rol_codigo, sucursal_id)
VALUES ('22222222-2222-2222-2222-222222222222','VEND01','Juan Pérez','$argon2id$fake',
        'vendedor','11111111-1111-1111-1111-111111111111');

INSERT INTO almacenes(id, codigo, nombre, tipo, responsable_id) VALUES
 ('33333333-3333-3333-3333-333333333333','BODEGA_PRINCIPAL','Bodega','bodega',NULL),
 ('44444444-4444-4444-4444-444444444444','CAMION_01','Camión 01','camion',
  '22222222-2222-2222-2222-222222222222');

INSERT INTO unidades_medida(codigo, nombre) VALUES ('PZA','Pieza'),('CAJA','Caja');
INSERT INTO productos(id, sku, nombre, unidad_base)
VALUES ('55555555-5555-5555-5555-555555555555','SKU-001','Frijol 1kg','PZA');
INSERT INTO producto_unidades(producto_id, unidad_codigo, factor, es_default) VALUES
 ('55555555-5555-5555-5555-555555555555','PZA',1,true),
 ('55555555-5555-5555-5555-555555555555','CAJA',24,false);

INSERT INTO dispositivos(id, usuario_id, etiqueta)
VALUES ('66666666-6666-6666-6666-666666666666','22222222-2222-2222-2222-222222222222','Moto G54');

INSERT INTO clientes(id, nombre_comercial, origen_alta, lat, lng, ubicacion_origen)
VALUES ('77777777-7777-7777-7777-777777777777','Abarrotes Doña Mary','campo',
        19.4326,-99.1332,'gps');

-- =============================================================================
-- INVARIANTE 1 — Reenviar el MISMO lote no duplica el ticket.
-- =============================================================================
-- El dispositivo genera el UUID. El servidor hace ON CONFLICT DO NOTHING.
-- Simula: la red cortó justo después del INSERT y el teléfono reenvió.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _insertar_venta_demo() RETURNS void LANGUAGE sql AS $$
    INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                        cliente_id, vendedor_id, almacen_id,
                        subtotal, total,
                        fecha_dispositivo, fecha_operativa, lat, lng)
    VALUES ('88888888-8888-8888-8888-888888888888',
            '66666666-6666-6666-6666-666666666666', 123, 'VEND01-000123',
            '77777777-7777-7777-7777-777777777777',
            '22222222-2222-2222-2222-222222222222',
            '44444444-4444-4444-4444-444444444444',
            250.00, 250.00, now(), CURRENT_DATE, 19.4326, -99.1332)
    ON CONFLICT (id) DO NOTHING;
$$;

SELECT _insertar_venta_demo();
SELECT _insertar_venta_demo();   -- reenvío
SELECT _insertar_venta_demo();   -- otro reenvío

\set QUIET off
\echo '--- INVARIANTE 1: 3 envíos del mismo ticket ---'
SELECT CASE WHEN count(*) = 1
            THEN 'PASA  · 3 envíos → 1 sola venta'
            ELSE 'FALLA · se duplicó el ticket: ' || count(*) END AS resultado
FROM ventas WHERE id = '88888888-8888-8888-8888-888888888888';

-- =============================================================================
-- INVARIANTE 2 — Un folio impreso NUNCA se reutiliza en el mismo dispositivo.
-- =============================================================================
-- Caso real: la app se reinstala y el contador local se reinicia. Sin esta
-- barrera, dos ventas distintas comparten el papel que tiene el cliente.
-- -----------------------------------------------------------------------------
\echo '--- INVARIANTE 2: folio repetido con UUID distinto ---'
DO $$
BEGIN
    INSERT INTO ventas (id, dispositivo_id, folio_consecutivo, folio_local,
                        cliente_id, vendedor_id, almacen_id,
                        subtotal, total,
                        fecha_dispositivo, fecha_operativa)
    VALUES (gen_random_uuid(),
            '66666666-6666-6666-6666-666666666666', 123, 'VEND01-000123',
            '77777777-7777-7777-7777-777777777777',
            '22222222-2222-2222-2222-222222222222',
            '44444444-4444-4444-4444-444444444444',
            999.00, 999.00, now(), CURRENT_DATE);
    RAISE WARNING 'FALLA · se aceptó un folio duplicado';
EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASA  · folio duplicado rechazado por uq_venta_folio_dispositivo';
END $$;

-- =============================================================================
-- INVARIANTE 3 — El libro mayor de inventario es inmutable.
-- =============================================================================
-- Un error se corrige con un movimiento de 'ajuste', jamás editando la
-- historia. Es lo que hace auditable el descuadre.
-- -----------------------------------------------------------------------------
INSERT INTO movimientos_inventario
    (tipo, almacen_origen_id, almacen_destino_id, producto_id, cantidad,
     documento_tipo, documento_id, usuario_id)
VALUES ('carga','33333333-3333-3333-3333-333333333333',
        '44444444-4444-4444-4444-444444444444',
        '55555555-5555-5555-5555-555555555555', 100,
        'carga', gen_random_uuid(), '22222222-2222-2222-2222-222222222222');

\echo '--- INVARIANTE 3: intento de UPDATE y DELETE sobre el libro mayor ---'
DO $$
BEGIN
    UPDATE movimientos_inventario SET cantidad = 1;
    RAISE WARNING 'FALLA · se permitió editar el libro mayor';
EXCEPTION WHEN others THEN
    RAISE NOTICE 'PASA  · UPDATE bloqueado (%)', left(SQLERRM, 60);
END $$;

DO $$
BEGIN
    DELETE FROM movimientos_inventario;
    RAISE WARNING 'FALLA · se permitió borrar del libro mayor';
EXCEPTION WHEN others THEN
    RAISE NOTICE 'PASA  · DELETE bloqueado (%)', left(SQLERRM, 60);
END $$;

-- =============================================================================
-- INVARIANTE 4 — La liquidación detecta el faltante aritméticamente.
-- =============================================================================
--   retornado - (cargado - vendido - merma + devuelto) = diferencia
-- Cargó 100, vendió 60, mermó 5, regresó 30 ⇒ faltan 5 piezas.
-- -----------------------------------------------------------------------------
INSERT INTO cargas (id, folio, almacen_origen_id, almacen_destino_id,
                    vendedor_id, fecha_operativa, estado)
VALUES ('99999999-9999-9999-9999-999999999999','CG-0001',
        '33333333-3333-3333-3333-333333333333',
        '44444444-4444-4444-4444-444444444444',
        '22222222-2222-2222-2222-222222222222', CURRENT_DATE, 'confirmada');

INSERT INTO liquidaciones (id, folio, carga_id, vendedor_id, fecha_operativa)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','LQ-0001',
        '99999999-9999-9999-9999-999999999999',
        '22222222-2222-2222-2222-222222222222', CURRENT_DATE);

INSERT INTO liquidacion_detalle
    (liquidacion_id, producto_id, cant_cargada, cant_vendida, cant_merma,
     cant_devuelta, cant_retornada)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        '55555555-5555-5555-5555-555555555555', 100, 60, 5, 0, 30);

\echo '--- INVARIANTE 4: cálculo de faltante en liquidación ---'
SELECT CASE WHEN diferencia = -5
            THEN 'PASA  · faltante detectado: ' || diferencia || ' pzas'
            ELSE 'FALLA · diferencia esperada -5, obtenida ' || diferencia END AS resultado
FROM liquidacion_detalle
WHERE liquidacion_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

-- =============================================================================
-- INVARIANTE 5 — Los rangos de folio de un dispositivo no se traslapan.
-- =============================================================================
-- Si se reinstala la app, el servidor entrega un rango nuevo. Traslapar
-- rangos es exactamente la forma de volver a emitir folios ya impresos.
-- -----------------------------------------------------------------------------
INSERT INTO folios_rangos (dispositivo_id, documento_tipo, desde, hasta)
VALUES ('66666666-6666-6666-6666-666666666666','venta', 1, 1000);

\echo '--- INVARIANTE 5: rango de folios traslapado ---'
DO $$
BEGIN
    INSERT INTO folios_rangos (dispositivo_id, documento_tipo, desde, hasta)
    VALUES ('66666666-6666-6666-6666-666666666666','venta', 500, 1500);
    RAISE WARNING 'FALLA · se aceptó un rango traslapado';
EXCEPTION WHEN exclusion_violation THEN
    RAISE NOTICE 'PASA  · rango traslapado rechazado';
END $$;

-- Un rango contiguo sí debe aceptarse.
INSERT INTO folios_rangos (dispositivo_id, documento_tipo, desde, hasta)
VALUES ('66666666-6666-6666-6666-666666666666','venta', 1001, 2000);
\echo 'PASA  · rango contiguo 1001-2000 aceptado'

-- =============================================================================
-- INVARIANTE 6 — Existencias en negativo SON posibles, a propósito.
-- =============================================================================
-- §0.1: el mundo físico ya ocurrió. Una venta offline que llega tarde no se
-- rechaza; el negativo queda visible y la liquidación lo cobra.
-- -----------------------------------------------------------------------------
INSERT INTO existencias (almacen_id, producto_id, cantidad)
VALUES ('44444444-4444-4444-4444-444444444444',
        '55555555-5555-5555-5555-555555555555', -3);

\echo '--- INVARIANTE 6: existencia negativa permitida y detectable ---'
SELECT CASE WHEN count(*) = 1
            THEN 'PASA  · negativo aceptado y visible en el índice de alertas'
            ELSE 'FALLA · el negativo fue rechazado o no es detectable' END AS resultado
FROM existencias
WHERE cantidad < 0 AND almacen_id = '44444444-4444-4444-4444-444444444444';

ROLLBACK;
