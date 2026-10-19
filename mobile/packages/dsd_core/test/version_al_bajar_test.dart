/// El teléfono dice su versión también al bajar (ADR 0002 §144).
///
/// Antes solo la decía al subir ventas: un teléfono recién actualizado que no
/// había vendido nada aparecía en el panel con la versión de cuando se registró.
library;

import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

import 'ayudas_sync.dart';

void main() {
  late Database db;

  setUp(() => db = baseLocal());
  tearDown(() => db.dispose());

  test('sin nada que subir, la versión va en el pull', () async {
    final transporte = TransporteFalso([Responde.pull(cambios: [])]);
    await armarSincronizador(db, transporte)
        .sincronizar(cursorActual: 0, appVersion: '0.1.0+47');
    final pulls = transporte.llamadas.where((l) => l.startsWith('/v1/sync/pull'));
    expect(pulls, isNotEmpty);
    expect(pulls.first, contains('app_version=0.1.0+47'));
  });

  test('sin versión conocida, el pull no la manda', () async {
    final transporte = TransporteFalso([Responde.pull(cambios: [])]);
    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);
    expect(
      transporte.llamadas.where((l) => l.startsWith('/v1/sync/pull')).first,
      isNot(contains('app_version')),
    );
  });
}
