/// Georreferencia: precisión, corrección manual y distancias.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  // Un punto real en la Ciudad de México: la corrección de longitud por latitud
  // importa aquí (un grado son ~105 km, no 111).
  Ubicacion cdmx({
    double lat = 19.4326,
    double lng = -99.1332,
    double? precision = 8.5,
    OrigenUbicacion origen = OrigenUbicacion.gps,
  }) =>
      Ubicacion(lat: lat, lng: lng, origen: origen, precisionMetros: precision);

  group('calidad de la lectura', () {
    test('los umbrales reflejan cómo se usa en la calle', () {
      expect(CalidadGps.de(8), equals(CalidadGps.buena));
      expect(CalidadGps.de(20), equals(CalidadGps.buena));
      expect(CalidadGps.de(35), equals(CalidadGps.aceptable));
      expect(CalidadGps.de(50), equals(CalidadGps.aceptable));
      expect(CalidadGps.de(120), equals(CalidadGps.mala));
      expect(CalidadGps.de(null), equals(CalidadGps.sinLectura));
    });

    test('solo se sugiere ajustar cuando la lectura no sirve', () {
      // Es una sugerencia, no un bloqueo: la tienda existe aunque el satélite
      // no colabore.
      expect(CalidadGps.buena.convieneAjustar, isFalse);
      expect(CalidadGps.aceptable.convieneAjustar, isFalse);
      expect(CalidadGps.mala.convieneAjustar, isTrue);
      expect(CalidadGps.sinLectura.convieneAjustar, isTrue);
    });
  });

  group('validación', () {
    test('rechaza coordenadas imposibles', () {
      expect(() => cdmx(lat: 91), throwsA(isA<UbicacionInvalida>()));
      expect(() => cdmx(lat: -91), throwsA(isA<UbicacionInvalida>()));
      expect(() => cdmx(lng: 181), throwsA(isA<UbicacionInvalida>()));
      expect(() => cdmx(precision: -1), throwsA(isA<UbicacionInvalida>()));
    });

    test('acepta los extremos válidos', () {
      expect(() => cdmx(lat: 90, lng: 180), returnsNormally);
      expect(() => cdmx(lat: -90, lng: -180), returnsNormally);
    });
  });

  group('payload', () {
    test('los números viajan como string con siete decimales', () {
      // El hash canónico prohíbe flotantes: un double en el payload haría que
      // el sobre no se pudiera firmar.
      final p = cdmx().aPayload();
      expect(p['lat'], equals('19.4326000'));
      expect(p['lng'], equals('-99.1332000'));
      expect(p['ubicacion_precision_m'], equals('8.50'));
      expect(p['ubicacion_origen'], equals('gps'));
    });

    test('sin precisión no se manda el campo', () {
      final p = cdmx(precision: null).aPayload();
      expect(p.containsKey('ubicacion_precision_m'), isFalse);
    });

    test('el payload se puede canonizar', () {
      // La prueba de fondo: si algo fuera double, esto lanzaría.
      expect(() => aTextoCanonico(cdmx().aPayload()), returnsNormally);
    });
  });

  group('corrección manual', () {
    test('desplazar al norte aumenta la latitud lo esperado', () {
      final original = cdmx();
      final movida = original.desplazada(norte: 100);
      // 100 m al norte ≈ 0.000898 grados de latitud.
      expect(movida.lat - original.lat, closeTo(0.000898, 0.00001));
      expect(movida.lng, closeTo(original.lng, 1e-9));
    });

    test('desplazar al este corrige por el coseno de la latitud', () {
      // En CDMX un grado de longitud son ~105 km, no 111: sin la corrección el
      // punto quedaría ~6% corto.
      final movida = cdmx().desplazada(este: 100);
      expect(movida.distanciaA(cdmx()), closeTo(100, 1));
    });

    test('la distancia recorrida es la pedida', () {
      final original = cdmx();
      for (final metros in [10.0, 50.0, 500.0]) {
        expect(
          original.desplazada(norte: metros).distanciaA(original),
          closeTo(metros, metros * 0.01),
        );
      }
    });

    test('corregir a mano cambia el origen y borra la precisión del GPS', () {
      // La precisión que reportaba el satélite ya no describe este punto, y que
      // fue una corrección humana tiene que quedar registrado.
      final movida = cdmx().desplazada(norte: 30);
      expect(movida.origen, equals(OrigenUbicacion.manual));
      expect(movida.precisionMetros, isNull);
      expect(movida.aPayload()['ubicacion_origen'], equals('manual'));
    });

    test('desplazar en los dos ejes acumula', () {
      final movida = cdmx().desplazada(norte: 30, este: 40);
      expect(movida.distanciaA(cdmx()), closeTo(50, 1));
    });

    test('en el polo no se rompe', () {
      // Caso degenerado: el coseno tiende a cero y la longitud deja de tener
      // sentido métrico. No debe producir infinitos ni lanzar.
      final polo = Ubicacion(lat: 90, lng: 0, origen: OrigenUbicacion.gps);
      final movida = polo.desplazada(este: 100);
      expect(movida.lng, equals(0));
      expect(movida.lat.isFinite, isTrue);
    });
  });

  group('distancias', () {
    test('a sí mismo es cero', () {
      expect(cdmx().distanciaA(cdmx()), closeTo(0, 0.01));
    });

    test('es simétrica', () {
      final a = cdmx();
      final b = cdmx(lat: 19.4400, lng: -99.1400);
      expect(a.distanciaA(b), closeTo(b.distanciaA(a), 0.01));
    });

    test('una distancia conocida cuadra', () {
      // CDMX (Zócalo) a Guadalajara (centro): ~460 km en línea recta.
      final zocalo = cdmx(lat: 19.4326, lng: -99.1332);
      final guadalajara = cdmx(lat: 20.6597, lng: -103.3496);
      expect(zocalo.distanciaA(guadalajara) / 1000, closeTo(460, 15));
    });

    test('distingue la cuadra de la esquina', () {
      // Lo que de verdad importa: a escala de tienda, unas decenas de metros.
      final tienda = cdmx();
      final vecina = tienda.desplazada(norte: 35);
      expect(tienda.distanciaA(vecina), closeTo(35, 1));
    });
  });
}
