/// Mitad Dart del contrato canónico.
///
/// Corre contra `contracts/canonical_vectors.json`, el MISMO archivo que
/// ejecuta `server/tests/test_canonico.py`. Si una suite se pone roja y la otra
/// no, hay divergencia entre los lenguajes — que es exactamente lo que este
/// contrato existe para atrapar.
///
/// Ejecutar:  flutter test test/canonico_test.dart
@Timeout(Duration(minutes: 2))
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import '../lib/dsd/canonico.dart';

void main() {
  final archivo = File('../contracts/canonical_vectors.json');
  final documento = jsonDecode(archivo.readAsStringSync()) as Map<String, dynamic>;
  final vectores = (documento['vectores'] as List).cast<Map<String, dynamic>>();

  test('el archivo de vectores está completo', () {
    expect(vectores.length, greaterThanOrEqualTo(30));
  });

  group('vectores compartidos', () {
    for (final vector in vectores) {
      final nombre = vector['nombre'] as String;
      final payload = (vector['payload'] as Map).cast<String, Object?>();

      test('$nombre · forma canónica', () {
        expect(aTextoCanonico(payload), equals(vector['canonico']));
      });

      test('$nombre · sha256', () {
        expect(hashPayload(payload), equals(vector['sha256']));
      });
    }
  });

  group('reglas', () {
    test('una clave nula equivale a una clave ausente', () {
      expect(hashPayload({'a': 1}), equals(hashPayload({'a': 1, 'b': null})));
    });

    test('los double están prohibidos', () {
      expect(() => aTextoCanonico({'total': 250.0}), throwsA(isA<PayloadNoCanonico>()));
    });

    test('el orden de inserción no cambia el hash', () {
      expect(hashPayload({'z': 1, 'a': 2}), equals(hashPayload({'a': 2, 'z': 1})));
    });

    test('el orden de un arreglo sí importa', () {
      expect(hashPayload({'x': [1, 2]}), isNot(equals(hashPayload({'x': [2, 1]}))));
    });

    test('las claves se ordenan por punto de código, no por UTF-16', () {
      // String.compareTo daría el orden inverso para el emoji.
      expect(aTextoCanonico({'\u{1F512}': 2, 'ﬀ': 1}), equals('{"ﬀ":1,"\u{1F512}":2}'));
    });

    test('el dinero conserva su escala', () {
      expect(formatearDinero('250'), equals('250.00'));
      expect(formatearDinero('-125.5'), equals('-125.50'));
      expect(formatearCantidad('1.375'), equals('1.375'));
      expect(formatearCantidad(12), equals('12.000'));
    });

    test('formatearDinero rechaza double', () {
      expect(() => formatearDinero(0.1 + 0.2), throwsA(isA<PayloadNoCanonico>()));
    });

    test('el instante se normaliza a UTC', () {
      final enMexico = DateTime.utc(2026, 9, 15, 3, 14, 7);
      expect(formatearInstante(enMexico), equals('2026-09-15T03:14:07.000Z'));
    });
  });
}
