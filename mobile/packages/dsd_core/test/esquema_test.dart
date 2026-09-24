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
}
