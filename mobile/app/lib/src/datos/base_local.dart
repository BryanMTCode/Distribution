/// Apertura de la base local.
///
/// En producción va cifrada con SQLCipher: en el teléfono viven precios,
/// márgenes, cartera y efectivo, y el equipo se pierde y se roba. En pruebas
/// se abre en memoria, sin cifrado, para que la suite corra en segundos.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqlite3/sqlite3.dart';

class BaseLocal {
  BaseLocal(this.db);

  final Database db;

  /// Base en memoria, para pruebas.
  factory BaseLocal.enMemoria() {
    final db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    return BaseLocal(db);
  }

  /// Base en disco. `llave` activa SQLCipher; sin ella la base queda en claro,
  /// que solo es aceptable en desarrollo.
  static Future<BaseLocal> abrir({String? llave}) async {
    final carpeta = await getApplicationDocumentsDirectory();
    final ruta = p.join(carpeta.path, 'dsd.db');
    final db = sqlite3.open(ruta);

    if (llave != null) {
      // PRAGMA key debe ir ANTES de cualquier otra sentencia, o SQLCipher
      // trabaja sobre una base que ya se abrió sin cifrar.
      db.execute("PRAGMA key = '$llave'");
    }
    db.execute('PRAGMA journal_mode = WAL');
    db.execute('PRAGMA foreign_keys = ON');
    db.execute(esquemaLocal);
    return BaseLocal(db);
  }

  void cerrar() => db.dispose();
}
