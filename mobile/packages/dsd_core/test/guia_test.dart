/// Cómo llegar a un cliente sin mapa ni señal (ADR 0002 §129).
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

// Mazatlán: la tienda de prueba y un punto 500 m al sur de ella.
const tiendaLat = 23.2494;
const tiendaLng = -106.4111;
const alSurLat = 23.2449; // ≈ 500 m al sur

void main() {
  group('en palabras', () {
    test('el rumbo, en ocho direcciones', () {
      expect(rumboEnPalabras(0), 'norte');
      expect(rumboEnPalabras(44), 'noreste');
      expect(rumboEnPalabras(90), 'este');
      expect(rumboEnPalabras(181), 'sur');
      expect(rumboEnPalabras(300), 'noroeste');
      expect(rumboEnPalabras(359), 'norte');
      expect(rumboEnPalabras(-90), 'oeste');
    });

    test('la distancia, como se dice en la calle', () {
      expect(distanciaEnPalabras(37), '37 m');
      expect(distanciaEnPalabras(347), '350 m');
      expect(distanciaEnPalabras(1234), '1.2 km');
      expect(distanciaEnPalabras(12600), '13 km');
    });

    test('hacia dónde, contra la marcha', () {
      expect(indicacionPara(5), Indicacion.deFrente);
      expect(indicacionPara(-40), Indicacion.ligeramenteIzquierda);
      expect(indicacionPara(95), Indicacion.derecha);
      expect(indicacionPara(-100), Indicacion.izquierda);
      expect(indicacionPara(170), Indicacion.atras);
      // 200° a la derecha es 160° a la izquierda: atrás; 330°, un poco a la izquierda.
      expect(indicacionPara(200), Indicacion.atras);
      expect(indicacionPara(330), Indicacion.ligeramenteIzquierda);
    });
  });

  group('la guía', () {
    test('quieto: la flecha contra el norte y el rumbo en palabras', () {
      final g = Guia.calcular(
        desdeLat: alSurLat, desdeLng: tiendaLng,
        hastaLat: tiendaLat, hastaLng: tiendaLng,
      );
      expect(g.distanciaMetros, closeTo(500, 10));
      expect(g.distanciaTexto, '500 m');
      expect(g.rumboTexto, 'norte');
      expect(g.orientadaPorMarcha, isFalse);
      expect(g.anguloDeLaFlecha, closeTo(0, 1));
      expect(g.indicacion, isNull);
      expect(g.llegaste, isFalse);
    });

    test('en marcha hacia el este, la tienda al norte queda a la izquierda', () {
      final g = Guia.calcular(
        desdeLat: alSurLat, desdeLng: tiendaLng,
        hastaLat: tiendaLat, hastaLng: tiendaLng,
        rumboDeMarcha: 90, velocidad: 8,
      );
      expect(g.orientadaPorMarcha, isTrue);
      expect(g.anguloDeLaFlecha, closeTo(-90, 1));
      expect(g.indicacion, Indicacion.izquierda);
    });

    test('demasiado despacio, el rumbo de marcha no se usa', () {
      final g = Guia.calcular(
        desdeLat: alSurLat, desdeLng: tiendaLng,
        hastaLat: tiendaLat, hastaLng: tiendaLng,
        rumboDeMarcha: 90, velocidad: 0.3,
      );
      expect(g.orientadaPorMarcha, isFalse);
      expect(g.indicacion, isNull);
    });

    test('a 20 m, o dentro del error del GPS, ya llegó', () {
      final cerca = Guia.calcular(
        desdeLat: tiendaLat - 0.00018, desdeLng: tiendaLng,
        hastaLat: tiendaLat, hastaLng: tiendaLng,
      );
      expect(cerca.distanciaMetros, lessThan(30));
      expect(cerca.llegaste, isTrue);
      final conError = Guia.calcular(
        desdeLat: tiendaLat - 0.0005, desdeLng: tiendaLng,
        hastaLat: tiendaLat, hastaLng: tiendaLng, precisionMetros: 80,
      );
      expect(conError.distanciaMetros, closeTo(55, 5));
      expect(conError.llegaste, isTrue);
    });
  });
}
