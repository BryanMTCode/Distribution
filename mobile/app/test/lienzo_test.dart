/// El lienzo espacial.
///
/// Un `CustomPaint` no se puede inspeccionar píxel por píxel en una prueba de
/// widget, así que lo que se verifica es lo que sí importa y sí se puede:
///
/// · Que aparezca cuando hay ubicación y no cuando no la hay.
/// · Que el radio dibujado abarque al vecino más lejano —si no, el punto queda
///   pegado al borde y el lienzo miente sobre la distancia—.
/// · Que la leyenda diga en palabras lo mismo que el dibujo, porque un dibujo
///   sin leyenda obliga a adivinar qué significa cada color.
/// · Que al ajustar el punto, los vecinos cambien de posición.
library;

import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_app/src/pantallas/vendedor/lienzo_espacial.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

final _aqui = Ubicacion(
  lat: 19.4326,
  lng: -99.1332,
  origen: OrigenUbicacion.gps,
  precisionMetros: 9,
);

/// Entra y abre la pantalla de alta con el GPS respondiendo.
Future<void> abrirAlta(
  WidgetTester tester, {
  LecturaGps gps = const GpsSinLectura(),
  void Function(dynamic base)? sembrar,
}) async {
  await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (base) {
      sembrarCliente(base, id: 'c-1', nombre: 'Ya registrada', secuencia: 1);
      sembrar?.call(base);
    },
    extras: [
      servicioUbicacionProvider
          .overrideWithValue(ServicioUbicacionFalso.siempre(gps)),
    ],
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('boton_nuevo_cliente'));
}

void main() {
  group('el radio que se dibuja', () {
    test('sin vecinos usa el mínimo, no un círculo de cero', () {
      // Un radio de cero divide entre cero al colocar puntos.
      final lienzo = LienzoEspacial(centro: _aqui, vecinos: const []);
      expect(lienzo.radioMetros, equals(radioLienzoMinimoMetros));
    });

    test('abarca al vecino más lejano con holgura', () {
      // Si el radio fuera exactamente la distancia del más lejano, ese punto
      // quedaría pegado al borde y el lienzo mentiría sobre qué tan lejos está.
      final lienzo = LienzoEspacial(
        centro: _aqui,
        vecinos: [
          _vecino(distancia: 40),
          _vecino(distancia: 180),
        ],
      );
      expect(lienzo.radioMetros, greaterThan(180));
      expect(lienzo.radioMetros, closeTo(225, 1));
    });

    test('un vecino muy lejano no comprime el dibujo hasta hacerlo ilegible', () {
      final lienzo = LienzoEspacial(
        centro: _aqui,
        vecinos: [_vecino(distancia: 5000)],
      );
      expect(lienzo.radioMetros, equals(radioLienzoMaximoMetros));
    });

    test('un vecino muy cerca no reduce el radio por debajo del mínimo', () {
      final lienzo = LienzoEspacial(
        centro: _aqui,
        vecinos: [_vecino(distancia: 3)],
      );
      expect(lienzo.radioMetros, equals(radioLienzoMinimoMetros));
    });
  });

  group('en la pantalla de alta', () {
    testWidgets('con GPS aparece el lienzo', (tester) async {
      await abrirAlta(tester, gps: GpsObtenido(_aqui));
      expect(find.byKey(const Key('lienzo_espacial')), findsOneWidget);
    });

    testWidgets('sin coordenadas no se dibuja nada', (tester) async {
      // Un lienzo centrado en la nada no orienta: desorienta.
      await abrirAlta(tester, gps: const GpsSinLectura());
      expect(find.byKey(const Key('lienzo_espacial')), findsNothing);
    });

    testWidgets('la leyenda cuenta los vecinos por cercanía', (tester) async {
      // Dos dentro del radio de duplicado y uno lejos.
      await abrirAlta(
        tester,
        gps: GpsObtenido(_aqui),
        sembrar: (base) {
          final cerca1 = _aqui.desplazada(norte: 25);
          final cerca2 = _aqui.desplazada(este: 40);
          final lejos = _aqui.desplazada(norte: 250);
          sembrarCliente(base, id: 'v-1', nombre: 'Vecina uno',
              lat: cerca1.lat, lng: cerca1.lng);
          sembrarCliente(base, id: 'v-2', nombre: 'Vecina dos',
              lat: cerca2.lat, lng: cerca2.lng);
          sembrarCliente(base, id: 'v-3', nombre: 'La de allá',
              lat: lejos.lat, lng: lejos.lng);
        },
      );

      expect(find.byKey(const Key('leyenda_lienzo')), findsOneWidget);
      expect(textoQueContiene('2 a menos de 60 m'), findsOneWidget);
      expect(textoQueContiene('1 más lejos'), findsOneWidget);
    });

    testWidgets('sin clientes a la redonda lo dice con palabras',
        (tester) async {
      // Un lienzo vacío sin explicación parece que la app no cargó.
      await abrirAlta(tester, gps: GpsObtenido(_aqui));
      expect(find.byKey(const Key('leyenda_lienzo_vacio')), findsOneWidget);
      expect(textoQueContiene('No hay clientes tuyos registrados'),
          findsOneWidget);
    });

    testWidgets('ajustar el punto mueve a los vecinos', (tester) async {
      // Es la razón de ser del lienzo: el movimiento confirma que el ajuste va
      // para el lado correcto. Se comprueba por la distancia que reporta la
      // leyenda, que es el mismo cálculo que alimenta el dibujo.
      await abrirAlta(
        tester,
        gps: GpsObtenido(_aqui),
        sembrar: (base) {
          final vecina = _aqui.desplazada(norte: 55);
          sembrarCliente(base, id: 'v-1', nombre: 'Vecina',
              lat: vecina.lat, lng: vecina.lng);
        },
      );
      // Arranca dentro del radio de duplicado.
      expect(textoQueContiene('1 a menos de 60 m'), findsOneWidget);

      // Alejarse 50 m al sur la saca del radio: 55 + 50 = 105 m.
      await tester.tap(find.text('50 m'));
      await tester.pumpAndSettle();
      await tocar(tester, const Key('mover_sur'));

      expect(textoQueContiene('1 más lejos'), findsOneWidget);
    });
  });
}

PosibleDuplicado _vecino({required double distancia, double rumbo = 0}) =>
    PosibleDuplicado(
      clienteId: 'v',
      nombreComercial: 'Vecina',
      distanciaMetros: distancia,
      rumboGrados: rumbo,
    );
