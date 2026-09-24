/// Genera `lib/src/esquema_local.dart` desde `mobile/db/schema.sql`.
///
/// El `.sql` sigue siendo la fuente de verdad —se lee, se revisa en diff y se
/// aplica con `sqlite3` en las pruebas—, pero un teléfono no puede leer un
/// archivo del repositorio en tiempo de ejecución. Se embebe como constante.
///
/// `test/esquema_test.dart` verifica que la constante corresponda al archivo,
/// así que no pueden separarse sin que el CI se ponga rojo.
///
/// Uso:  dart run tool/generar_esquema.dart
library;

import 'dart:io';

void main() {
  final sql = File('../../db/schema.sql').readAsStringSync();
  final salida = File('lib/src/esquema_local.dart');

  salida.writeAsStringSync('''
// GENERADO — no editar a mano.
// Fuente: mobile/db/schema.sql
// Regenerar: dart run tool/generar_esquema.dart

/// Esquema de la base local del dispositivo (SQLite + SQLCipher).
///
/// Dos zonas con reglas opuestas: el ESPEJO (catálogo, precios, clientes,
/// saldos) se sobrescribe con lo que manda el servidor, y la zona PROPIA
/// (ventas, cobros, mermas, no-drops, clientes nuevos) nace aquí y solo viaja
/// hacia el servidor. Nadie edita lo mismo desde dos lados.
library;

const esquemaLocal = r\'\'\'
$sql\'\'\';
''');
  stdout.writeln('esquema embebido: ${sql.length} caracteres');
}
