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

  test('UNA BASE VIEJA RECIBE clientes.plan_visita', () {
    // El aplicador de clientes la escribe en cada delta de cliente: sin ella, el
    // primer pull después de actualizar la app reventaría.
    aplicarEsquemaLocal(db);
    db.execute('ALTER TABLE clientes DROP COLUMN plan_visita');
    expect(columnasDe('clientes'), isNot(contains('plan_visita')));
    aplicarEsquemaLocal(db);
    expect(columnasDe('clientes'), contains('plan_visita'));
  });

  test('UNA BASE VIEJA RECIBE el artículo y la diferencia de los ajustes (§145)', () {
    // El aplicador de `ajuste_camion` las escribe: sin ellas, el primer ajuste
    // de la oficina después de actualizar la app reventaría el pull. Los
    // ajustes de antes se quedan, con las columnas en NULL.
    aplicarEsquemaLocal(db);
    db.execute('ALTER TABLE ajustes_camion_aplicados DROP COLUMN producto_id');
    db.execute('ALTER TABLE ajustes_camion_aplicados DROP COLUMN delta');
    db.execute(
      "INSERT INTO ajustes_camion_aplicados (ajuste_id, folio, nota, aplicado_en) "
      "VALUES ('aj-1', 'AC-000001', 'de antes', '2026-10-05T18:00:00.000Z')",
    );

    aplicarEsquemaLocal(db);

    final fila = db.select('SELECT * FROM ajustes_camion_aplicados').single;
    expect(fila['folio'], equals('AC-000001'), reason: 'no se pierde la fila');
    expect(fila['producto_id'], isNull);
    expect(fila['delta'], isNull);
  });

  test('UNA BASE VIEJA DEJA DE EXIGIR GPS EN EL NO-DROP (§158)', () {
    // SQLite no deja quitar un NOT NULL con un ALTER: la tabla se rehace. Se
    // finge la vieja con el esquema de hoy y las dos columnas obligatorias, como
    // estaban hasta la +51, y con un no-drop de antes que no se puede perder.
    aplicarEsquemaLocal(db);
    final hoy = RegExp(r'CREATE TABLE IF NOT EXISTS no_drops \([\s\S]*?\n\);')
        .firstMatch(esquemaLocal)!
        .group(0)!;
    final vieja = hoy
        .replaceFirst(RegExp(r'lat( +)REAL,'), r'lat REAL NOT NULL,')
        .replaceFirst(RegExp(r'lng( +)REAL,'), r'lng REAL NOT NULL,');
    expect(vieja, isNot(equals(hoy)));
    db.execute('DROP TABLE no_drops');
    db.execute(vieja);
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial) VALUES ('cli-1', 'La Esquina')",
    );
    db.execute(
      "INSERT INTO motivos_no_drop (codigo, nombre, categoria, requiere_nota) "
      "VALUES ('CERRADO', 'Cerrado', 'cliente', 0)",
    );
    db.execute(
      "INSERT INTO no_drops (id, folio_consecutivo, cliente_id, motivo_codigo, lat, "
      "lng, fecha_dispositivo, fecha_operativa) "
      "VALUES ('nd-1', 1, 'cli-1', 'CERRADO', 23.2, -106.4, 'x', '2026-10-19')",
    );
    bool exige(String columna) => db
        .select('PRAGMA table_info(no_drops)')
        .any((f) => f['name'] == columna && f['notnull'] == 1);
    expect(exige('lat'), isTrue);

    aplicarEsquemaLocal(db);

    expect(exige('lat'), isFalse, reason: 'sin GPS el vendedor no cerraría al cliente');
    expect(exige('lng'), isFalse);
    final fila = db.select('SELECT * FROM no_drops').single;
    expect(fila['id'], equals('nd-1'), reason: 'no se pierde la fila');
    expect(fila['lat'], equals(23.2));
    expect(fila['ubicacion_origen'], equals('gps'));
    expect(
      db.select("SELECT name FROM sqlite_master WHERE name LIKE 'no_drops%'"),
      hasLength(1),
      reason: 'no queda la tabla de paso',
    );
    expect(db.select('PRAGMA foreign_keys').single.values.first, equals(1));

    // Y ya se puede registrar sin ubicación.
    db.execute(
      "INSERT INTO no_drops (id, folio_consecutivo, cliente_id, motivo_codigo, "
      "fecha_dispositivo, fecha_operativa) "
      "VALUES ('nd-2', 2, 'cli-1', 'CERRADO', 'x', '2026-10-20')",
    );
    expect(db.select('SELECT * FROM no_drops'), hasLength(2));
  });

  test('UNA BASE VIEJA RECIBE ventas.modificaciones (§162)', () {
    // Agregar a una venta la escribe; sin ella, el primer «Agregar artículos»
    // después de actualizar reventaría con el cliente enfrente.
    aplicarEsquemaLocal(db);
    db.execute('ALTER TABLE ventas DROP COLUMN modificaciones');
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
    expect(fila['modificaciones'], 0);
    expect(fila['total'], 592.0, reason: 'no se pierde la venta');
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
