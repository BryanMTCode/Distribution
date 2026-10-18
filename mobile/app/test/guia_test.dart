/// Cómo llegar a un cliente, sin mapa y sin señal (ADR 0002 §129).
///
/// · La tienda con ubicación tiene «Cómo llegar» en la lista y en su perfil;
///   la que no tiene, no.
/// · Quieto: la distancia, el rumbo en palabras y la flecha contra el norte.
/// · En marcha: «a tu izquierda», contra hacia donde va.
/// · Al llegar, lo dice.
/// · Sin lectura, o con el GPS apagado, lo dice sin alarmar, y deja volver a
///   intentar sin salir.
/// · «Abrir en Mapas» le pasa la coordenada a la app de mapas; si no hay, lo
///   dice y manda a la flecha.
/// · Cabe en el Hyundai HYLine Pro (360 de ancho).
/// · «Cerca de mí»: la lista de la más cercana a la más lejana, con su
///   distancia; las que no tienen ubicación al final, y otro toque la regresa.
library;

import 'package:dsd_app/src/datos/abridor_de_mapas.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_app/src/pantallas/vendedor/guia.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

// Mazatlán: la tienda, y un punto unos 500 m al sur.
const _tiendaLat = 23.2494;
const _tiendaLng = -106.4111;

GpsObtenido _gps(double lat, double lng, {double? rumbo, double? velocidad, double prec = 8}) =>
    GpsObtenido(
      Ubicacion(lat: lat, lng: lng, origen: OrigenUbicacion.gps, precisionMetros: prec),
      rumboDeMarcha: rumbo,
      velocidad: velocidad,
    );

Future<(ServicioUbicacionFalso, AbridorDeMapasFalso)> _entrar(
  WidgetTester tester, {
  List<LecturaGps> lecturas = const [],
  bool hayAppDeMapas = true,
}) async {
  final gps = ServicioUbicacionFalso(lecturas);
  final mapas = AbridorDeMapasFalso(hayApp: hayAppDeMapas);
  await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Abarrotes La Esperanza', secuencia: 1,
          lat: _tiendaLat, lng: _tiendaLng, ubicacionOrigen: 'gps');
      sembrarCliente(base, id: 'c2', nombre: 'Sin ubicación todavía', secuencia: 2);
    },
    extras: [
      servicioUbicacionProvider.overrideWithValue(gps),
      abridorDeMapasProvider.overrideWithValue(mapas),
    ],
  );
  await entrarCon(tester, pinCorrecto);
  return (gps, mapas);
}

void main() {
  testWidgets('«Cómo llegar» está en la que tiene ubicación, y no en la otra', (tester) async {
    await _entrar(tester);
    expect(find.byKey(const Key('guia_c1')), findsOneWidget);
    expect(find.byKey(const Key('guia_c2')), findsNothing);
    // La ubicación sigue a un toque en las dos.
    expect(find.byKey(const Key('ubicar_c1')), findsOneWidget);
    expect(find.byKey(const Key('ubicar_c2')), findsOneWidget);
  });

  testWidgets('quieto: a cuánto, hacia dónde y la flecha contra el norte', (tester) async {
    await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    await tester.tap(find.byKey(const Key('guia_c1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pantalla_guia')), findsOneWidget);
    expect(find.text('500 m'), findsOneWidget);
    expect(find.text('al norte'), findsOneWidget);
    expect(find.byKey(const Key('guia_norte')), findsOneWidget);
    expect(textoQueContiene('Arriba es el norte'), findsOneWidget);
    expect(textoQueContiene('GPS ±8 m'), findsOneWidget);
  });

  testWidgets('en marcha hacia el este, la tienda al norte queda a la izquierda',
      (tester) async {
    final (gps, _) = await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    await tester.tap(find.byKey(const Key('guia_c1')));
    await tester.pumpAndSettle();
    gps.empujar(_gps(23.2449, -106.4105, rumbo: 90, velocidad: 9));
    await tester.pumpAndSettle();
    expect(find.text('a tu izquierda'), findsOneWidget);
    expect(find.byKey(const Key('guia_norte')), findsNothing);
    expect(textoQueContiene('arriba es de frente'), findsOneWidget);
    final flecha = tester.widget<Transform>(find.ancestor(
      of: find.byKey(const Key('guia_icono_flecha')),
      matching: find.byType(Transform),
    ).first);
    // Girada un cuarto de vuelta a la izquierda.
    expect(flecha.transform.getRotation().entry(0, 1), closeTo(1, 0.05));
  });

  testWidgets('al llegar, lo dice', (tester) async {
    await _entrar(tester, lecturas: [_gps(_tiendaLat - 0.0001, _tiendaLng)]);
    await tester.tap(find.byKey(const Key('guia_c1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('guia_llegaste')), findsOneWidget);
    expect(find.text('Ya llegaste'), findsOneWidget);
    await tester.tap(find.byKey(const Key('guia_volver')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pantalla_guia')), findsNothing);
  });

  testWidgets('sin lectura, la busca; con el GPS apagado, lo dice', (tester) async {
    final (gps, _) = await _entrar(tester);
    await tester.tap(find.byKey(const Key('guia_c1')));
    // Hay un indicador girando: no se espera a que se calme, solo a que termine
    // de entrar la pantalla.
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const Key('guia_buscando')), findsOneWidget);
    expect(textoQueContiene('no necesita señal de datos'), findsOneWidget);
    gps.empujar(const GpsApagado());
    await tester.pumpAndSettle();
    expect(textoQueContiene('La ubicación del teléfono está apagada'), findsOneWidget);
    // La prende y vuelve a intentar sin salir de la pantalla.
    expect(gps.seguidores, 1);
    await tester.tap(find.byKey(const Key('guia_reintentar')));
    await tester.pump();
    expect(gps.seguidores, 2);
    expect(find.byKey(const Key('guia_buscando')), findsOneWidget);
    gps.empujar(_gps(23.2449, _tiendaLng));
    await tester.pumpAndSettle();
    expect(find.text('500 m'), findsOneWidget);
  });

  testWidgets('«Abrir en Mapas» le pasa la tienda a la app de mapas', (tester) async {
    final (_, mapas) = await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    await tester.tap(find.byKey(const Key('guia_c1')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('abrir_en_mapas')));
    await tester.tap(find.byKey(const Key('abrir_en_mapas')));
    await tester.pumpAndSettle();
    expect(mapas.abiertos, [(_tiendaLat, _tiendaLng, 'Abarrotes La Esperanza')]);
    expect(textoQueContiene('descargas una vez el mapa de Mazatlán'), findsOneWidget);
  });

  testWidgets('sin app de mapas lo dice, y manda a la flecha', (tester) async {
    await _entrar(tester, hayAppDeMapas: false);
    await tester.tap(find.byKey(const Key('ubicar_c1')));
    await tester.pumpAndSettle();
    // Desde el perfil también.
    expect(find.byKey(const Key('perfil_guiame')), findsOneWidget);
    await tester.tap(find.byKey(const Key('perfil_abrir_en_mapas')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('no tiene una app de mapas'), findsOneWidget);
  });

  testWidgets('la guía cabe en el Hyundai HYLine Pro (360 de ancho)', (tester) async {
    final (gps, _) = await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    tester.view.physicalSize = const Size(360, 760);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('guia_c1')));
    await tester.pumpAndSettle();
    gps.empujar(_gps(23.2449, -106.4105, rumbo: 90, velocidad: 9));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.byKey(const Key('guia_distancia')), findsOneWidget);
  });

  group('cerca de mí', () {
    Future<void> entrarConTiendas(
      WidgetTester tester, {
      LecturaGps gps = const GpsSinLectura(),
      bool conPlan = false,
    }) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          // Al norte de donde está el vendedor: a 2 km, a 300 m y a 1 km.
          sembrarCliente(base, id: 'lejos', nombre: 'La Lejana', secuencia: 1,
              lat: _tiendaLat + 0.018, lng: _tiendaLng);
          sembrarCliente(base, id: 'cerca', nombre: 'La Cercana', secuencia: 2,
              lat: _tiendaLat + 0.0027, lng: _tiendaLng);
          sembrarCliente(base, id: 'media', nombre: 'La de en medio', secuencia: 3,
              lat: _tiendaLat + 0.009, lng: _tiendaLng);
          sembrarCliente(base, id: 'sin', nombre: 'Sin ubicación', secuencia: 4);
          if (conPlan) {
            // Jueves, el día del reloj de prueba.
            base.db.execute(
              "UPDATE clientes SET plan_visita = '[{\"dia\":4,\"semana\":null}]'",
            );
          }
        },
        extras: [
          servicioUbicacionProvider.overrideWithValue(ServicioUbicacionFalso.siempre(gps)),
          abridorDeMapasProvider.overrideWithValue(AbridorDeMapasFalso()),
        ],
      );
      await entrarCon(tester, pinCorrecto);
    }

    List<String> orden(WidgetTester tester) {
      final nombres = ['La Lejana', 'La Cercana', 'La de en medio', 'Sin ubicación'];
      return nombres..sort((a, b) => tester.getTopLeft(find.text(a)).dy
          .compareTo(tester.getTopLeft(find.text(b)).dy));
    }

    testWidgets('de la más cercana a la más lejana, con su distancia', (tester) async {
      await entrarConTiendas(tester, gps: _gps(_tiendaLat, _tiendaLng));
      expect(orden(tester), ['La Lejana', 'La Cercana', 'La de en medio', 'Sin ubicación']);
      await tester.tap(find.byKey(const Key('cerca_de_mi')));
      await tester.pumpAndSettle();
      expect(orden(tester), ['La Cercana', 'La de en medio', 'La Lejana', 'Sin ubicación']);
      expect(textoQueContiene('a 300 m'), findsOneWidget);
      expect(textoQueContiene('a 1.0 km'), findsOneWidget);
      expect(textoQueContiene('a 2.0 km'), findsOneWidget);
      // Otro toque: en el orden de la ruta, sin distancias.
      await tester.tap(find.byKey(const Key('cerca_de_mi')));
      await tester.pumpAndSettle();
      expect(orden(tester), ['La Lejana', 'La Cercana', 'La de en medio', 'Sin ubicación']);
      expect(textoQueContiene('a 300 m'), findsNothing);
    });

    testWidgets('sin satélites lo dice y la lista se queda como estaba', (tester) async {
      await entrarConTiendas(tester);
      await tester.tap(find.byKey(const Key('cerca_de_mi')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('no encontró los satélites'), findsOneWidget);
      expect(orden(tester).first, 'La Lejana');
    });

    testWidgets('con «Hoy» y «Cerca de mí» cabe en 360 de ancho', (tester) async {
      await entrarConTiendas(tester, gps: _gps(_tiendaLat, _tiendaLng), conPlan: true);
      tester.view.physicalSize = const Size(360, 760);
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('filtro_hoy')), findsOneWidget);
      await tester.tap(find.byKey(const Key('cerca_de_mi')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(textoQueContiene('a 300 m'), findsOneWidget);
    });
  });
}
