/// La constante embebida y el .sql no pueden separarse.
library;

import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  test('la constante corresponde al archivo fuente', () {
    // Si esto falla, alguien editó el .sql y no regeneró la constante: la app
    // en el teléfono estaría creando un esquema distinto al que se prueba.
    final archivo = File('../../db/schema.sql').readAsStringSync();
    expect(esquemaLocal, equals(archivo),
        reason: 'corre: dart run tool/generar_esquema.dart');
  });

  test('el esquema embebido se aplica en SQLite', () {
    final db = sqlite3.openInMemory();
    addTearDown(db.dispose);
    db.execute(esquemaLocal);

    final tablas = db
        .select("SELECT name FROM sqlite_master WHERE type='table'")
        .map((f) => f['name'] as String)
        .toSet();
    // Las piezas sin las que la app no puede operar sin señal.
    expect(tablas, containsAll(['outbox', 'clientes', 'ventas', 'credencial_local']));
  });

  test('el esquema se aplica DOS VECES sobre la misma base', () {
    // `BaseLocal.abrir` ejecuta el esquema en CADA arranque de la app, así que
    // tiene que ser idempotente. No lo era: los 32 CREATE iban sin
    // `IF NOT EXISTS`, y el segundo arranque moría con «table sync_estado
    // already exists» DENTRO de `main()`, antes de `runApp`.
    //
    // Sin árbol de widgets no hay pantalla de error: el vendedor veía NEGRO.
    // La app se instalaba, abría bien una vez, y no volvía a abrir nunca.
    //
    // La prueba de arriba no lo encontraba porque aplica el esquema una sola
    // vez sobre una base nueva, que es justo el caso que funciona.
    final archivo = File('../../db/schema.sql').path;
    final ruta = '${Directory.systemTemp.createTempSync('dsd_esquema').path}/dsd.db';
    expect(File(archivo).existsSync(), isTrue);

    for (final arranque in [1, 2, 3]) {
      final db = sqlite3.open(ruta);
      expect(() => db.execute(esquemaLocal), returnsNormally,
          reason: 'el arranque $arranque del esquema falló: dejaría la app en '
              'pantalla negra desde el segundo arranque');
      db.dispose();
    }
  });

  test('los datos sobreviven al siguiente arranque', () {
    // El corolario del anterior: si para hacerlo idempotente alguien pusiera
    // `DROP TABLE` antes de cada `CREATE`, esta prueba se pondría roja — y con
    // razón, porque eso borraría las ventas no sincronizadas en cada arranque.
    final ruta =
        '${Directory.systemTemp.createTempSync('dsd_datos').path}/dsd.db';

    var db = sqlite3.open(ruta);
    db.execute(esquemaLocal);
    db.execute("INSERT INTO sync_estado (clave, valor) VALUES ('dispositivo_id', 'abc')");
    db.dispose();

    db = sqlite3.open(ruta);
    db.execute(esquemaLocal);
    final valor = db
        .select("SELECT valor FROM sync_estado WHERE clave = 'dispositivo_id'")
        .single['valor'];
    db.dispose();

    expect(valor, equals('abc'),
        reason: 'el esquema borró datos al reabrir: el teléfono perdería las '
            'ventas que no hubiera subido');
  });
}
