/// Una columna nueva sobre una base que ya existía.
///
/// ───────────────────────────────────────────────────────────────────────────
/// EL BUG QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA
/// ───────────────────────────────────────────────────────────────────────────
/// `esquemaLocal` es idempotente porque todo es `CREATE ... IF NOT EXISTS`, y eso
/// basta para una tabla nueva. Para una COLUMNA nueva no basta: la tabla ya existe,
/// el `CREATE` no hace nada, y la columna nunca llega.
///
/// La forma en que se descubre es la peor: la app se instala encima, arranca bien,
/// y revienta días después al escribir esa columna — en la calle, con el cliente
/// enfrente. Esta prueba finge la base vieja, que es el único modo de verlo sin un
/// teléfono viejo en la mano.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;

  setUp(() => db = sqlite3.openInMemory());
  tearDown(() => db.dispose());

  List<String> columnasDe(String tabla) => db
      .select('PRAGMA table_info($tabla)')
      .map((f) => f['name'] as String)
      .toList();

  test('una base NUEVA ya trae todas las columnas', () {
    aplicarEsquemaLocal(db);
    expect(columnasDe('ventas'), contains('nota_oficina'));
  });

  test('UNA BASE VIEJA RECIBE LA COLUMNA QUE LE FALTA', () {
    // El teléfono viejo se finge quitándole la columna a la base de hoy, no
    // escribiendo a mano la tabla de la versión anterior: una copia a mano se
    // queda desactualizada y la prueba acabaría probando un esquema que ya no
    // existe.
    aplicarEsquemaLocal(db);
    db.execute('ALTER TABLE ventas DROP COLUMN nota_oficina');
    expect(columnasDe('ventas'), isNot(contains('nota_oficina')));

    aplicarEsquemaLocal(db);

    expect(
      columnasDe('ventas'),
      contains('nota_oficina'),
      reason: 'la app habría reventado al escribirla, en la calle',
    );
  });

  test('UNA BASE VIEJA RECIBE clientes.por_confirmar', () {
    // Octubre 2026: lo que el cliente pagó por transferencia y la oficina no ha
    // confirmado. El aplicador de cartera lo escribe en cada pull: sin la
    // columna, el primer pull después de actualizar la app reventaría.
    aplicarEsquemaLocal(db);
    db.execute('ALTER TABLE clientes DROP COLUMN por_confirmar');
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial, saldo_cache) "
      "VALUES ('cli-1', 'La Esquina', 1200)",
    );

    aplicarEsquemaLocal(db);

    final fila = db.select('SELECT * FROM clientes').single;
    expect(fila['por_confirmar'], equals(0.0));
    expect(fila['saldo_cache'], equals(1200.0), reason: 'no se pierde la fila');
  });

  test('aplicarlo dos veces no truena', () {
    // Es lo que pasa en cada arranque de la app.
    aplicarEsquemaLocal(db);
    expect(() => aplicarEsquemaLocal(db), returnsNormally);
  });

  test('la fila vieja queda con la columna en NULL, no se pierde', () {
    aplicarEsquemaLocal(db);
    db.execute('ALTER TABLE ventas DROP COLUMN nota_oficina');
    // El cliente, porque la venta lo referencia: la base del teléfono sí tiene
    // esa llave foránea.
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial) VALUES ('cli-1', 'La Esquina')",
    );
    db.execute(
      "INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, total, "
      "fecha_dispositivo, fecha_operativa, creado_en) "
      "VALUES ('v-1', 1, 'VEND01-000001', 'cli-1', 592.0, 'x', '2026-10-05', 'x')",
    );

    aplicarEsquemaLocal(db);

    final fila = db.select('SELECT * FROM ventas').single;
    expect(fila['folio_local'], equals('VEND01-000001'));
    expect(fila['nota_oficina'], isNull);
  });
}
