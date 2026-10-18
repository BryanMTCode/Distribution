/// El mapa dentro de la app, sin señal (ADR 0002 §130).
///
/// · Desde la lista, «Mapa»: las tiendas con ubicación como pines (las que no
///   tienen, no) y el vendedor como punto azul; sin el mapa de calles, el aviso
///   para bajarlo.
/// · Tocar una tienda abre su ficha con la distancia, y «Guíame» va a la flecha.
/// · Bajar el mapa: el avance en MB, y al terminar queda guardado y con calles.
///   Si llega dañado o cortado no se guarda nada; sin mapa en el servidor o sin
///   conexión, lo dice; en todos los casos se puede volver a intentar.
/// · Con el mapa ya guardado: las calles, el crédito de OpenStreetMap, y
///   «Volver a bajar el mapa» con su fecha; el nuevo cambia las calles, y si
///   falla, el aviso se cierra y queda el que había.
/// · Desde la guía, «Ver en el mapa»: la tienda marcada y la línea hasta el
///   vendedor.
/// · Cabe en el Hyundai HYLine Pro (360 de ancho).
/// · La fuente HTTP: el archivo con la credencial, y una renovación si venció.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:dsd_app/src/datos/abridor_de_mapas.dart';
import 'package:dsd_app/src/datos/mapa_offline.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_app/src/estado/mapa.dart';
import 'package:dsd_app/src/pantallas/vendedor/guia.dart';
import 'package:dsd_app/src/pantallas/vendedor/mapa.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'ayudas.dart';

const _tiendaLat = 23.2494;
const _tiendaLng = -106.4111;

/// El servidor, de mentiras: lo que diga `info` y el archivo por pedazos.
class _FuenteFalsa implements FuenteDelMapa {
  _FuenteFalsa({this.contenido, this.disponible = true, this.sinConexion = false,
      this.shaQueDice, this.version = '20261017'});

  final List<int>? contenido;
  final String version;
  final bool disponible;
  final bool sinConexion;
  final String? shaQueDice;
  int infos = 0;

  /// Si se da, el archivo sale de aquí y la prueba decide cuándo llega cada
  /// pedazo.
  StreamController<List<int>>? enCamino;

  @override
  Future<MapaDelServidor> info() async {
    infos++;
    if (sinConexion) throw const ErrorDeRed('sin señal');
    if (!disponible) return const MapaDelServidor(disponible: false);
    final c = contenido!;
    return MapaDelServidor(
      disponible: true,
      version: version,
      bytes: c.length,
      sha256: shaQueDice ?? sha256.convert(c).toString(),
      atribucion: '© OpenStreetMap',
    );
  }

  @override
  Future<ArchivoEnCamino> bajar() async {
    final pedazos = enCamino?.stream ??
        Stream.fromIterable([
          for (var i = 0; i < contenido!.length; i += 65536)
            contenido!.sublist(i, (i + 65536).clamp(0, contenido!.length)),
        ]);
    return ArchivoEnCamino(200, pedazos);
  }
}

List<int> _archivoDePrueba(int bytes) => List<int>.generate(bytes, (i) => (i * 31) % 251);

Future<(ServicioUbicacionFalso, Directory)> _entrar(
  WidgetTester tester, {
  List<LecturaGps> lecturas = const [],
  FuenteDelMapa? fuente,
  bool conMapa = false,
  List<String>? versionesDibujadas,
}) async {
  final carpeta = Directory.systemTemp.createTempSync('mapa_de_prueba');
  addTearDown(() => carpeta.deleteSync(recursive: true));
  if (conMapa) {
    File('${carpeta.path}/mazatlan.pmtiles').writeAsBytesSync(_archivoDePrueba(1000));
    File('${carpeta.path}/mazatlan.json').writeAsStringSync(jsonEncode({'version': '20261017'}));
  }
  final gps = ServicioUbicacionFalso(lecturas);
  await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Abarrotes La Esperanza', secuencia: 1,
          lat: _tiendaLat, lng: _tiendaLng, ubicacionOrigen: 'gps', codigo: '0101');
      sembrarCliente(base, id: 'c2', nombre: 'Sin ubicación todavía', secuencia: 2);
      sembrarCliente(base, id: 'c3', nombre: 'Tienda Los Pinos', secuencia: 3,
          lat: _tiendaLat + 0.004, lng: _tiendaLng + 0.003);
      // La Esperanza toca hoy (jueves, el día del reloj de prueba).
      base.db.execute(
        "UPDATE clientes SET plan_visita = '[{\"dia\":4,\"semana\":null}]' WHERE id = 'c1'",
      );
    },
    extras: [
      servicioUbicacionProvider.overrideWithValue(gps),
      abridorDeMapasProvider.overrideWithValue(AbridorDeMapasFalso()),
      carpetaDeMapasProvider.overrideWith((_) async => carpeta),
      fuenteDelMapaProvider.overrideWithValue(fuente ?? _FuenteFalsa(contenido: [1, 2, 3])),
      // Las calles de verdad las dibuja vector_map_tiles con un archivo real;
      // aquí basta saber si la pantalla las puso.
      capaDeCallesProvider.overrideWith((ref, mapa) async {
        versionesDibujadas?.add(mapa.version);
        return const SizedBox.expand(key: Key('capa_calles'));
      }),
    ],
  );
  await entrarCon(tester, pinCorrecto);
  return (gps, carpeta);
}

GpsObtenido _gps(double lat, double lng) => GpsObtenido(
      Ubicacion(lat: lat, lng: lng, origen: OrigenUbicacion.gps, precisionMetros: 8),
    );

Future<void> _abrirMapa(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('abrir_mapa')));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('el mapa pone las tiendas con ubicación y al vendedor', (tester) async {
    await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    await _abrirMapa(tester);
    expect(find.byKey(const Key('pantalla_mapa')), findsOneWidget);
    expect(find.byKey(const Key('pin_c1')), findsOneWidget);
    expect(find.byKey(const Key('pin_c3')), findsOneWidget);
    expect(find.byKey(const Key('pin_c2')), findsNothing);
    expect(find.byKey(const Key('mapa_yo')), findsOneWidget);
    expect(find.byKey(const Key('mapa_leyenda')), findsOneWidget);
    // Sin el mapa de calles: el aviso para bajarlo, y nada de calles.
    expect(find.byKey(const Key('mapa_descargar')), findsOneWidget);
    expect(textoQueContiene('Bájalo una vez con WiFi'), findsOneWidget);
    expect(find.byKey(const Key('capa_calles')), findsNothing);
    expect(find.byKey(const Key('mapa_atribucion')), findsNothing);
  });

  testWidgets('tocar una tienda abre su ficha, y «Guíame» va a la flecha', (tester) async {
    await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('pin_c1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ficha_mapa_c1')), findsOneWidget);
    expect(textoQueContiene('a 500 m · 0101 · le toca hoy'), findsOneWidget);
    await tester.tap(find.byKey(const Key('mapa_guiame')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pantalla_guia')), findsOneWidget);
    expect(find.text('500 m'), findsOneWidget);
  });

  testWidgets('bajar el mapa: el avance, y queda guardado y con calles', (tester) async {
    final contenido = _archivoDePrueba(1200000);
    final fuente = _FuenteFalsa(contenido: contenido)
      ..enCamino = StreamController<List<int>>();
    final (_, carpeta) = await _entrar(tester, fuente: fuente);
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('mapa_descargar')));
    await tester.pump();
    fuente.enCamino!.add(contenido.sublist(0, 600000));
    await tester.pump();
    expect(find.byKey(const Key('mapa_bajando')), findsOneWidget);
    expect(textoQueContiene('0.6 MB de 1.2 MB'), findsOneWidget);
    // A media descarga no hay mapa a medias.
    expect(File('${carpeta.path}/mazatlan.pmtiles').existsSync(), isFalse);

    fuente.enCamino!.add(contenido.sublist(600000));
    await fuente.enCamino!.close();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('mapa_listo')), findsOneWidget);
    expect(find.byKey(const Key('capa_calles')), findsOneWidget);
    expect(find.byKey(const Key('mapa_atribucion')), findsOneWidget);
    expect(File('${carpeta.path}/mazatlan.pmtiles').readAsBytesSync(), contenido);
    expect(File('${carpeta.path}/mazatlan.pmtiles.parcial').existsSync(), isFalse);
  });

  testWidgets('si llega dañado no se guarda, y se puede volver a intentar', (tester) async {
    final fuente = _FuenteFalsa(contenido: _archivoDePrueba(1000), shaQueDice: 'otra');
    final (_, carpeta) = await _entrar(tester, fuente: fuente);
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('mapa_descargar')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('El mapa llegó dañado'), findsOneWidget);
    expect(carpeta.listSync(), isEmpty);
    await tester.tap(find.byKey(const Key('mapa_reintentar')));
    await tester.pumpAndSettle();
    expect(fuente.infos, 2);
  });

  testWidgets('sin mapa en el servidor lo dice', (tester) async {
    await _entrar(tester, fuente: _FuenteFalsa(disponible: false));
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('mapa_descargar')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('La oficina todavía no ha bajado el mapa'), findsOneWidget);
  });

  testWidgets('sin conexión lo dice', (tester) async {
    await _entrar(tester, fuente: _FuenteFalsa(sinConexion: true));
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('mapa_descargar')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('Conéctate a un WiFi'), findsOneWidget);
  });

  testWidgets('con el mapa guardado: calles, crédito y «Volver a bajar»', (tester) async {
    await _entrar(tester, conMapa: true);
    await _abrirMapa(tester);
    expect(find.byKey(const Key('capa_calles')), findsOneWidget);
    expect(find.byKey(const Key('mapa_atribucion')), findsOneWidget);
    expect(find.byKey(const Key('mapa_aviso')), findsNothing);
    await tester.tap(find.byKey(const Key('mapa_menu')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('mapa_actualizar')), findsOneWidget);
    expect(textoQueContiene('El de 17/10/2026'), findsOneWidget);
  });

  testWidgets('bajar uno nuevo cambia las calles a la versión nueva', (tester) async {
    final versiones = <String>[];
    final contenido = _archivoDePrueba(5000);
    await _entrar(tester, conMapa: true, versionesDibujadas: versiones,
        fuente: _FuenteFalsa(contenido: contenido, version: '20261101'));
    await _abrirMapa(tester);
    expect(versiones, ['20261017']);
    await tester.tap(find.byKey(const Key('mapa_menu')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('mapa_actualizar')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('mapa_listo')), findsOneWidget);
    expect(versiones, ['20261017', '20261101']);
  });

  testWidgets('con un mapa guardado, el error de una actualización se cierra',
      (tester) async {
    await _entrar(tester, conMapa: true, fuente: _FuenteFalsa(sinConexion: true));
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('mapa_menu')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('mapa_actualizar')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('Conéctate a un WiFi'), findsOneWidget);
    await tester.tap(find.byKey(const Key('mapa_cerrar_error')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('mapa_aviso')), findsNothing);
    // El mapa que había sigue ahí.
    expect(find.byKey(const Key('capa_calles')), findsOneWidget);
  });

  testWidgets('desde la guía: la tienda marcada y la línea hasta el vendedor',
      (tester) async {
    await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    await tester.tap(find.byKey(const Key('guia_c1')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('guia_ver_en_mapa')));
    await tester.tap(find.byKey(const Key('guia_ver_en_mapa')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pantalla_mapa')), findsOneWidget);
    expect(
      find.descendant(of: find.byType(AppBar), matching: find.text('Abarrotes La Esperanza')),
      findsOneWidget,
    );
    expect(find.byKey(const Key('mapa_linea_al_destino')), findsOneWidget);
    expect(find.byKey(const Key('pin_c1')), findsOneWidget);
  });

  testWidgets('«Dónde estoy» sin GPS lo dice', (tester) async {
    await _entrar(tester);
    await _abrirMapa(tester);
    await tester.tap(find.byKey(const Key('mapa_centrar_en_mi')));
    await tester.pump();
    expect(textoQueContiene('Buscando tu ubicación'), findsOneWidget);
  });

  testWidgets('cabe en el Hyundai HYLine Pro (360 de ancho)', (tester) async {
    await _entrar(tester, lecturas: [_gps(23.2449, _tiendaLng)]);
    tester.view.physicalSize = const Size(360, 760);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await _abrirMapa(tester);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byKey(const Key('pin_c1')));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.byKey(const Key('mapa_guiame')), findsOneWidget);
  });

  group('la fuente HTTP', () {
    test('pide el archivo con la credencial, y renueva una vez si venció', () async {
      var token = 'viejo';
      var renovaciones = 0;
      final vistos = <String?>[];
      final cliente = MockClient.streaming((peticion, _) async {
        vistos.add(peticion.headers['Authorization']);
        final vale = peticion.headers['Authorization'] == 'Bearer nuevo';
        return http.StreamedResponse(
          Stream.value(vale ? [1, 2, 3] : <int>[]),
          vale ? 200 : 401,
        );
      });
      final fuente = FuenteDelMapaHttp(
        baseUrl: 'https://api.ejemplo',
        transporte: () => null,
        leerToken: () => token,
        renovar: () async {
          renovaciones++;
          token = 'nuevo';
          return true;
        },
        cliente: cliente,
      );
      final archivo = await fuente.bajar();
      expect(archivo.codigo, 200);
      expect(await archivo.pedazos.expand((p) => p).toList(), [1, 2, 3]);
      expect(vistos, ['Bearer viejo', 'Bearer nuevo']);
      expect(renovaciones, 1);
    });

    test('sin sesión y sin poder renovarla, lo dice', () async {
      final fuente = FuenteDelMapaHttp(
        baseUrl: 'https://api.ejemplo',
        transporte: () => null,
        leerToken: () => null,
        renovar: () async => false,
        cliente: MockClient((_) async => http.Response('', 401)),
      );
      await expectLater(fuente.info(), throwsA(isA<SinSesionParaElMapa>()));
      await expectLater(fuente.bajar(), throwsA(isA<SinSesionParaElMapa>()));
    });
  });

}
