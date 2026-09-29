/// Consecutivos locales del dispositivo.
///
/// El folio que se imprime en el papel lo genera el teléfono, pero **dentro de
/// un rango que asigna el servidor**. Es la defensa contra el escenario que
/// casi nadie prueba: se reinstala la app, el contador local vuelve a 1, y el
/// equipo empieza a reimprimir folios que ya están en papel en manos de
/// clientes.
///
/// El consecutivo global lo pone el servidor al recibir. Si el teléfono lo
/// adivinara, dos vendedores offline emitirían el mismo número el mismo día.
library;

import 'package:sqlite3/sqlite3.dart';

class RangoAgotado implements Exception {
  const RangoAgotado(this.tipo, this.hasta);

  final String tipo;
  final int hasta;

  @override
  String toString() =>
      'se agotó el rango de folios de $tipo (terminaba en $hasta); '
      'hay que pedir uno nuevo al servidor';
}

/// Rango de folios para un tipo de documento.
class RangoFolios {
  RangoFolios({
    required this.tipo,
    required this.desde,
    required this.hasta,
    required int consumidoHasta,
  })  : _consumidoHasta = consumidoHasta,
        assert(hasta > desde, 'rango inválido') {
    if (consumidoHasta < desde - 1 || consumidoHasta > hasta) {
      throw ArgumentError('consumidoHasta fuera del rango $desde..$hasta');
    }
  }

  final String tipo;
  final int desde;
  final int hasta;
  int _consumidoHasta;

  int get consumidoHasta => _consumidoHasta;
  int get restantes => hasta - _consumidoHasta;
  bool get agotado => restantes <= 0;

  /// Avisa cuándo pedir un rango nuevo. Quedarse sin folios a media ruta
  /// significa no poder vender, así que se pide con holgura.
  bool get porAgotarse => restantes <= 50;

  /// Toma el siguiente consecutivo. **No es idempotente a propósito**: cada
  /// llamada quema un folio. El llamador la invoca una sola vez, dentro de la
  /// misma transacción que escribe el documento.
  int tomar() {
    if (agotado) throw RangoAgotado(tipo, hasta);
    return ++_consumidoHasta;
  }

  /// El folio impreso: 'VEND01-000124'.
  static String formatear(String codigoVendedor, int consecutivo) =>
      '$codigoVendedor-${consecutivo.toString().padLeft(6, '0')}';
}

/// Los rangos guardados en la base local.
///
/// `consumido_hasta` se persiste **dentro de la misma transacción** que escribe
/// el documento. Esa es la propiedad que hace segura la numeración impresa:
///
/// · Si la transacción se confirma, el folio queda quemado y el papel existe.
/// · Si se deshace, la marca no avanzó y el siguiente intento **reutiliza el
///   mismo número**. Sin hueco y sin duplicado.
///
/// Por eso el rango se lee de la base en cada venta y no se guarda en memoria
/// entre ventas: un objeto de larga vida se adelantaría en un rollback y dejaría
/// huecos en la numeración que nadie podría explicar en una auditoría.
class RepoFolios {
  const RepoFolios(this._db);

  final Database _db;

  /// Lee el rango vigente, o `null` si el servidor no ha asignado ninguno.
  ///
  /// Sin rango no se puede vender, y eso se le dice al vendedor con esas
  /// palabras: "sincroniza una vez para recibir folios".
  RangoFolios? leer(String tipo) {
    final filas = _db.select(
      'SELECT desde, hasta, consumido_hasta FROM folios_rangos WHERE tipo = ?',
      [tipo],
    );
    if (filas.isEmpty) return null;
    final f = filas.first;
    return RangoFolios(
      tipo: tipo,
      desde: f['desde'] as int,
      hasta: f['hasta'] as int,
      consumidoHasta: f['consumido_hasta'] as int,
    );
  }

  /// Guarda un rango nuevo, tal como lo asignó el servidor.
  void guardar(
    RangoFolios rango, {
    required String asignadoEn,
  }) {
    _db.execute(
      '''
      INSERT INTO folios_rangos (tipo, desde, hasta, consumido_hasta, asignado_en)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(tipo) DO UPDATE SET
        desde = excluded.desde,
        hasta = excluded.hasta,
        consumido_hasta = excluded.consumido_hasta,
        asignado_en = excluded.asignado_en
      ''',
      [rango.tipo, rango.desde, rango.hasta, rango.consumidoHasta, asignadoEn],
    );
  }
}
