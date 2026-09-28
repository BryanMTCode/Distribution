-- =============================================================================
-- 0012 · Importes rígidos: la aritmética de la partida, como constraint
-- =============================================================================
-- Regla de negocio cerrada (ADR 0002 §7): EL VENDEDOR NO OTORGA DESCUENTOS EN
-- LA CALLE. El precio unitario es el de la lista del cliente, sin excepción,
-- sin campo editable y sin flujo de autorización.
--
-- Esa regla se implementa en tres lugares, y aquí está el tercero:
--
--   1. El teléfono no tiene dónde escribir un precio (dsd_core/carrito.dart:
--      `agregar()` recibe la presentación y la cantidad, nunca un precio).
--   2. El servidor recalcula al recibir (app/domain/importes.py).
--   3. PostgreSQL lo verifica al escribir. Este archivo.
--
-- -----------------------------------------------------------------------------
-- ¿UN CHECK NO VIOLA EL PRINCIPIO §0.1 ("el servidor nunca rechaza")?
-- -----------------------------------------------------------------------------
-- No, y la distinción es la que sostiene todo el diseño:
--
--   · Una REGLA DE NEGOCIO incumplida —el cliente se pasó de su límite, el
--     precio estaba desactualizado, la venta quedó fuera de la geocerca— se
--     MARCA (requiere_revision). La mercancía ya salió del camión; rechazarla
--     no la devuelve, solo borra el registro de que salió.
--
--   · Una INCOHERENCIA ARITMÉTICA no es un hecho del mundo físico: es un bug
--     del cliente o una manipulación del payload. `importe` distinto de
--     `cantidad × precio` no describe ninguna venta posible. Guardarlo sería
--     meter a la contabilidad un número que no se puede explicar.
--
-- Y no se pierde: el motor de sincronización envuelve cada sobre en su propio
-- SAVEPOINT y manda a `sync_cuarentena` lo que viola un constraint, con el
-- payload íntegro. La venta queda visible, revisable y reprocesable — que es
-- exactamente lo que §0.1 exige. Lo que no queda es silenciosamente mal sumada.
--
-- -----------------------------------------------------------------------------
-- POR QUÉ `descuento` SIGUE EN LA FÓRMULA SI SIEMPRE ES CERO
-- -----------------------------------------------------------------------------
-- Porque el día que la oficina active una promoción (`promociones` ya existe:
-- nxm, regalo), el descuento lo pondrá LA OFICINA, no el vendedor, y la
-- aritmética tiene que seguir cuadrando. Un CHECK que exigiera `descuento = 0`
-- rechazaría esas ventas legítimas, y eso sí sería violar §0.1. La regla que se
-- sella es "el vendedor no decide el precio", no "el descuento no existe".
-- =============================================================================

-- -----------------------------------------------------------------------------
-- El importe de la partida
-- -----------------------------------------------------------------------------
-- Escala fija: cantidad numeric(14,3) × precio_unitario numeric(14,4) es una
-- multiplicación EXACTA en numeric (no hay float en el camino), y ROUND() sobre
-- numeric redondea medio ALEJÁNDOSE de cero. Dart hace lo mismo con enteros y
-- Python con ROUND_HALF_UP explícito — el modo por omisión de Decimal es
-- medio-al-par y daría 0.12 donde estos dos dan 0.13.
--
-- El redondeo es UNO, al final. No se redondea el precio antes de multiplicar:
-- una caja de 24 piezas a 12.3333 vale 296.00, no 295.92.
ALTER TABLE venta_partidas
    ADD CONSTRAINT partida_importe_coherente
    CHECK (importe = ROUND(cantidad * precio_unitario, 2) - descuento);

-- -----------------------------------------------------------------------------
-- Lo que salió del camión
-- -----------------------------------------------------------------------------
-- `cantidad_base` es la cantidad en unidad base, congelada al vender. Si no
-- cuadra con cantidad × factor, el movimiento de inventario y la partida
-- cuentan historias distintas, y el descuadre aparece en la liquidación del
-- final del día sin forma de investigarlo.
ALTER TABLE venta_partidas
    ADD CONSTRAINT partida_cantidad_base_coherente
    CHECK (cantidad_base = ROUND(cantidad * factor_unidad, 3));

-- -----------------------------------------------------------------------------
-- LO QUE AQUÍ **NO** SE PUSO, Y POR QUÉ
-- -----------------------------------------------------------------------------
-- La primera versión de esta migración exigía además que toda venta trajera su
-- `lista_precios_id` y su versión, con el argumento de que un precio sin lista
-- no se puede auditar contra nada. Es verdad que no se puede auditar, y es la
-- razón equivocada para un CHECK.
--
-- Una venta a la que le falta ese dato **sigue siendo una venta que ocurrió**:
-- la mercancía salió del camión y el cliente tiene su remisión impresa. Que le
-- falte metadato de trazabilidad es un incumplimiento de regla, no una
-- imposibilidad aritmética — y las reglas se MARCAN, no se rechazan (§0.1).
-- Rechazarla mandaría a cuarentena, por un campo administrativo, una venta
-- perfectamente sumada.
--
-- Va entonces como motivo de revisión en el manejador de `venta.crear`
-- ('sin_lista_de_precios'), no como constraint. La distinción es la misma que
-- sostiene los dos CHECK de arriba: `importe` que no cuadra no describe ninguna
-- venta posible; una venta sin lista sí describe una, mal registrada.

COMMENT ON CONSTRAINT partida_importe_coherente ON venta_partidas IS
    'importe = ROUND(cantidad * precio_unitario, 2) - descuento. Espejo de '
    'app/domain/importes.py y de dsd_core/precio.dart. Los tres deben dar el '
    'mismo centavo; contracts/importes_de_ejemplo.json es el árbitro.';
