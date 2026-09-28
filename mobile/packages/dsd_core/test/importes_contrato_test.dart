/// El quinto contrato: la aritmética de una partida.
///
/// `contracts/importes_de_ejemplo.json` lo genera Python con `Decimal`. Esta
/// suite lo consume con enteros. Si las dos implementaciones se separan en un
/// centavo, esta prueba se pone roja **antes** de que una venta legítima caiga
/// en revisión por un redondeo.
///
/// Es el mismo patrón de los otros cuatro contratos: un archivo versionado, dos
/// lenguajes que lo ejecutan, y un paso de frescura en CI que impide cambiarlo
/// sin que se note.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  final documento = jsonDecode(
    File('../../../contracts/importes_de_ejemplo.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  final casos = (documento['casos'] as List).cast<Map<String, dynamic>>();

  test('el archivo de contrato trae casos', () {
    // Si el generador se rompiera y escribiera una lista vacía, todas las
    // pruebas de abajo "pasarían" sin ejecutar nada.
    expect(casos, isNotEmpty);
  });

  for (final caso in casos) {
    final nombre = caso['nombre'] as String;

    test('importe · $nombre', () {
      final cantidad = Cantidad.deTexto(caso['cantidad'] as String);
      final precio = Precio.deTexto(caso['precio_unitario'] as String);

      expect(
        importeDeLinea(cantidad, precio).texto,
        equals(caso['importe']),
        reason: caso['por_que'] as String,
      );
    });

    test('cantidad base · $nombre', () {
      final cantidad = Cantidad.deTexto(caso['cantidad'] as String);
      final factor = Factor.deTexto(caso['factor_unidad'] as String);

      expect(
        cantidadBase(cantidad, factor).texto,
        equals(caso['cantidad_base']),
        reason: caso['por_que'] as String,
      );
    });
  }
}
