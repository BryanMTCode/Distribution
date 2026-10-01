/// Apertura de la base local.
///
/// En producción va cifrada con SQLCipher: en el teléfono viven precios,
/// márgenes, cartera y efectivo, y el equipo se pierde y se roba. En pruebas
/// se abre en memoria, sin cifrado, para que la suite corra en segundos.
library;

import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqlite3/sqlite3.dart';

class BaseLocal {
  BaseLocal(this.db, {this.ruta});

  final Database db;

  /// Dónde vive el archivo. `null` cuando la base es en memoria (pruebas).
  ///
  /// Hace falta para el borrado remoto: borrar las tablas no basta —SQLite
  /// conserva el archivo y sus páginas liberadas, y un análisis forense puede
  /// recuperar de ahí lo que había—. El archivo se borra entero.
  final String? ruta;

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
    return BaseLocal(db, ruta: ruta);
  }

  void cerrar() => db.dispose();

  /// Borra la base de este teléfono, sin vuelta atrás (Fase 9).
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// POR QUÉ NO BASTA BORRAR EL ARCHIVO, Y POR QUÉ NO SE CIERRA LA BASE
  /// ───────────────────────────────────────────────────────────────────────
  /// Las dos mitades de esto son correcciones a la versión obvia, y la segunda
  /// la encontró una prueba de widget reventando:
  ///
  /// **No basta un `DELETE FROM`.** SQLite conserva las páginas liberadas con
  /// su contenido dentro, y un análisis del aparato recupera de ahí lo que
  /// había. Lo mismo el `-wal` y el `-shm`, que son copias de las últimas
  /// transacciones — justamente las ventas del día que acabamos de subir. Así
  /// que se enciende `secure_delete` (SQLite sobreescribe lo que libera), se
  /// vacían las tablas, se compacta con `VACUUM` y se borran los tres archivos.
  ///
  /// **No se cierra la base, y eso es deliberado.** La primera versión hacía
  /// `dispose()` y la app reventaba inmediatamente después del borrado: los
  /// providers de la pantalla del vendedor —la cola pendiente, la lista de
  /// clientes— se recalculan en el mismo cuadro en que el árbol cambia a la
  /// pantalla de «equipo borrado», y leían una base ya cerrada. En un teléfono
  /// real eso es una pantalla roja justo después de un borrado exitoso, que es
  /// el peor momento posible para una excepción: parece que el borrado falló.
  ///
  /// Dejando el handle abierto sobre una base VACÍA, esas lecturas devuelven
  /// cero renglones y la transición es limpia. Los archivos ya están
  /// desenlazados del sistema de archivos, así que nadie más puede abrirlos, y
  /// el inodo desaparece cuando el proceso termina. La siguiente apertura de la
  /// app crea una base nueva — y sin credencial en el Keystore, nadie entra.
  ///
  /// Devuelve cuántos archivos se borraron. Cero significa que no había ninguno
  /// (una base en memoria, o un borrado que ya ocurrió), y no es un error: este
  /// método tiene que poder llamarse dos veces.
  int borrarTodo() {
    // `secure_delete` antes de borrar nada: encendido después, ya no alcanza a
    // las páginas que se liberaron antes.
    db.execute('PRAGMA secure_delete = ON');

    final tablas = db
        .select(
          "SELECT name FROM sqlite_master "
          " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
        )
        .map((f) => f['name'] as String)
        .toList();

    // `foreign_keys = OFF` mientras se vacía: con las llaves activas habría que
    // borrar en orden topológico, y un orden escrito a mano se rompe en cuanto
    // alguien agrega una tabla.
    db.execute('PRAGMA foreign_keys = OFF');
    for (final tabla in tablas) {
      db.execute('DELETE FROM "$tabla"');
    }
    db.execute('PRAGMA foreign_keys = ON');

    // Compacta: devuelve al archivo el espacio de las páginas liberadas, ya
    // sobreescritas por `secure_delete`.
    db.execute('VACUUM');

    final destino = ruta;
    if (destino == null) return 0;

    var borrados = 0;
    for (final sufijo in ['', '-wal', '-shm']) {
      final archivo = File('$destino$sufijo');
      if (archivo.existsSync()) {
        archivo.deleteSync();
        borrados++;
      }
    }
    return borrados;
  }
}
