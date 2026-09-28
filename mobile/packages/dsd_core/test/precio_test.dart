/// Precio, cantidad, factor y la aritmética de una partida.
///
/// Lo que se prueba aquí no es que la multiplicación funcione: es que el
/// redondeo dé **el mismo centavo** que Python y que PostgreSQL. Una diferencia
/// de un centavo manda ventas legítimas a revisión, y una bandera de revisión
/// que se enciende sola se acaba ignorando.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  group('Precio', () {
    test('conserva los cuatro decimales', () {
      expect(Precio.deTexto('12.3333').diezmilesimos, equals(123333));
      expect(Precio.deTexto('12.3333').texto, equals('12.3333'));
      expect(Precio.deTexto('0.0000'), equals(Precio.cero));
      expect(Precio.dePesos(15).texto, equals('15.0000'));
    });

    test('rechaza lo que no tiene exactamente cuatro decimales', () {
      // El contrato define la forma exacta. Aceptar '12.33' aquí escondería una
      // divergencia con el string que Python emite.
      for (final malo in ['12.33', '12.33333', '12', '12.3333 ', ' 12.3333', '-1.0000', '1,3333']) {
        expect(
          () => Precio.deTexto(malo),
          throwsA(isA<FormatException>()),
          reason: "'$malo' no debería pasar",
        );
      }
    });

    test('el precio por pieza derivado de la caja sobrevive el viaje', () {
      // 296.00 / 24 = 12.333333… La oficina lo captura truncado a 4 decimales,
      // y el REAL de SQLite tiene que devolverlo idéntico.
      final desdeBase = Precio.deBase(12.3333);
      expect(desdeBase.texto, equals('12.3333'));
      expect(Precio.deBase(0.0001).texto, equals('0.0001'));
      expect(Precio.deBase(9999.9999).texto, equals('9999.9999'));
    });

    test('en pantalla se muestra corto solo si no miente', () {
      expect(Precio.deTexto('15.5000').textoCorto, equals('15.50'));
      expect(Precio.deTexto('15.0000').textoCorto, equals('15.00'));
      // Aquí NO se puede acortar: mostrar 12.33 haría creer que la caja de 24
      // cuesta 295.92 cuando cuesta 296.00.
      expect(Precio.deTexto('12.3333').textoCorto, equals('12.3333'));
    });
  });

  group('Cantidad', () {
    test('va y viene con tres decimales', () {
      expect(Cantidad.deEnteros(3).texto, equals('3.000'));
      expect(Cantidad.deTexto('1.375').milesimos, equals(1375));
      expect(Cantidad.deBase(2.5).texto, equals('2.500'));
    });

    test('suma y compara sin double', () {
      final tres = Cantidad.deEnteros(3);
      final dos = Cantidad.deEnteros(2);
      expect((tres + dos).texto, equals('5.000'));
      expect((tres - dos).texto, equals('1.000'));
      expect(tres > dos, isTrue);
      expect(Cantidad.deTexto('0.100') + Cantidad.deTexto('0.200'),
          equals(Cantidad.deTexto('0.300')));
    });

    test('en pantalla se ve como la teclea el vendedor', () {
      expect(Cantidad.deEnteros(3).textoCorto, equals('3'));
      expect(Cantidad.deTexto('1.500').textoCorto, equals('1.500'));
    });
  });

  group('el importe de una partida', () {
    test('redondea UNA vez, al final', () {
      // El caso que justifica los 4 decimales: una caja de 24 piezas a
      // 12.3333 vale 296.00 exactos. Si el precio se hubiera guardado en
      // centavos (12.33), daría 295.92 — cuatro centavos por caja que el
      // cliente reclama con la lista en la mano.
      expect(
        importeDeLinea(Cantidad.deEnteros(24), Precio.deTexto('12.3333')),
        equals(Dinero.deTexto('296.00')),
      );
    });

    test('el medio va hacia arriba, igual que ROUND() de PostgreSQL', () {
      // 1 × 0.125 = 0.125 → 0.13.
      // Con el modo por omisión de Decimal en Python (medio al par) daría 0.12:
      // esa es exactamente la divergencia que este caso vigila.
      expect(
        importeDeLinea(Cantidad.deEnteros(1), Precio.deTexto('0.1250')),
        equals(Dinero.deTexto('0.13')),
      );
      expect(
        importeDeLinea(Cantidad.deEnteros(1), Precio.deTexto('0.1350')),
        equals(Dinero.deTexto('0.14')),
      );
      expect(
        importeDeLinea(Cantidad.deEnteros(3), Precio.deTexto('0.1250')),
        equals(Dinero.deTexto('0.38')), // 0.375 → 0.38
      );
    });

    test('casos que salen de la lista real', () {
      expect(
        importeDeLinea(Cantidad.deEnteros(1), Precio.deTexto('296.0000')),
        equals(Dinero.deTexto('296.00')),
      );
      expect(
        importeDeLinea(Cantidad.deEnteros(12), Precio.deTexto('18.5000')),
        equals(Dinero.deTexto('222.00')),
      );
      // Media caja de granel.
      expect(
        importeDeLinea(Cantidad.deTexto('0.500'), Precio.deTexto('45.8000')),
        equals(Dinero.deTexto('22.90')),
      );
    });

    test('un importe grande no desborda', () {
      // 99 999 piezas a 9 999.9999: muy por encima de cualquier venta real, y
      // el entero de 64 bits ni se entera.
      final importe =
          importeDeLinea(Cantidad.deEnteros(99999), Precio.deTexto('9999.9999'));
      expect(importe.texto, equals('999989990.00'));
    });
  });

  group('la cantidad que sale del camión', () {
    test('multiplica por el factor de la presentación', () {
      expect(
        cantidadBase(Cantidad.deEnteros(2), Factor.deEnteros(24)).texto,
        equals('48.000'),
      );
      expect(
        cantidadBase(Cantidad.deEnteros(3), Factor.uno).texto,
        equals('3.000'),
      );
    });

    test('un factor fraccionario no pierde milésimos', () {
      // Un display de 6.5 unidades base es raro, pero el esquema lo admite y
      // el descuadre de inventario no perdona.
      expect(
        cantidadBase(Cantidad.deEnteros(2), Factor.deTexto('6.5000')).texto,
        equals('13.000'),
      );
    });
  });

  group('Factor', () {
    test('se ve corto cuando es entero', () {
      expect(Factor.deEnteros(24).textoCorto, equals('24'));
      expect(Factor.deTexto('6.5000').textoCorto, equals('6.5000'));
      expect(Factor.uno.esUno, isTrue);
    });
  });
}
