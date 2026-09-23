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
