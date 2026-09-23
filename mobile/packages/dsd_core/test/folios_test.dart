/// Consecutivos locales.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  RangoFolios rango({int desde = 1, int hasta = 1000, int? consumido}) => RangoFolios(
        tipo: 'venta',
        desde: desde,
        hasta: hasta,
        consumidoHasta: consumido ?? desde - 1,
      );

  test('el primer folio es el inicio del rango', () {
    expect(rango().tomar(), equals(1));
  });

  test('los folios no se repiten', () {
    final r = rango();
    final tomados = List.generate(100, (_) => r.tomar());
    expect(tomados.toSet(), hasLength(100));
    expect(tomados.last, equals(100));
  });

  test('al agotarse avisa en vez de seguir', () {
    final r = rango(desde: 1, hasta: 3);
    r..tomar()..tomar()..tomar();
    expect(r.agotado, isTrue);
    expect(() => r.tomar(), throwsA(isA<RangoAgotado>()));
  });

  test('avisa con holgura para pedir uno nuevo', () {
    // Quedarse sin folios a media ruta significa no poder vender.
    expect(rango(hasta: 1000, consumido: 900).porAgotarse, isFalse);
    expect(rango(hasta: 1000, consumido: 951).porAgotarse, isTrue);
  });

  test('un rango nuevo tras reinstalar no repite folios del anterior', () {
    // El escenario que casi nadie prueba: el contador local vuelve a 1 y el
    // equipo reimprime folios que ya están en papel en manos de clientes.
    final anterior = rango(desde: 1, hasta: 1000, consumido: 137);
    final nuevo = rango(desde: 1001, hasta: 2000);
    expect(nuevo.tomar(), equals(1001));
    expect(nuevo.tomar(), greaterThan(anterior.consumidoHasta));
  });

  test('el folio impreso lleva el código del vendedor', () {
    expect(RangoFolios.formatear('VEND01', 124), equals('VEND01-000124'));
    expect(RangoFolios.formatear('VEND01', 1), equals('VEND01-000001'));
  });

  test('un rango mal formado se rechaza al construirlo', () {
    expect(
      () => RangoFolios(tipo: 'venta', desde: 10, hasta: 5, consumidoHasta: 9),
      throwsA(anyOf(isA<AssertionError>(), isA<ArgumentError>())),
    );
    expect(
      () => RangoFolios(tipo: 'venta', desde: 1, hasta: 100, consumidoHasta: 500),
      throwsArgumentError,
    );
  });
}
