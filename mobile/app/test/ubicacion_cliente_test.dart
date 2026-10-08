/// La ubicación del cliente en la pantalla: GPS o a mano (ADR 0002 §84).
///
/// · El vendedor, desde el perfil del cliente y sin señal: con el GPS, o
///   escribiéndola —o pegándola tal como la copia el mapa—. Se guarda al
///   momento y viaja por la cola.
/// · Una mal escrita se dice con palabras y no se guarda.
/// · La oficina la corrige desde la ficha del cliente.
library;

import 'dart:convert';

import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  group('el vendedor', () {
    Future<dynamic> abrirPerfil(WidgetTester tester, LecturaGps gps) async {
      final base = await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          sembrarEscenarioDeVenta(base);
          sembrarParaCobrar(base);
        },
        extras: [
          servicioUbicacionProvider.overrideWithValue(ServicioUbicacionFalso.siempre(gps)),
        ],
      );
      await entrarCon(tester, pinCorrecto);
      await tocar(tester, const Key('cliente_cliente-1'));
      await tester.tap(find.byKey(const Key('menu_de_visita')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('opcion_perfil_cliente')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_perfil_cliente')), findsOneWidget);
      return base;
    }

    Future<void> guardar(WidgetTester tester) async {
      await tester.ensureVisible(find.byKey(const Key('boton_guardar_ubicacion')));
      await tester.tap(find.byKey(const Key('boton_guardar_ubicacion')));
      await tester.pumpAndSettle();
    }

    testWidgets('con el GPS: se guarda al momento y viaja por la cola', (tester) async {
      final base = await abrirPerfil(
        tester,
        GpsObtenido(Ubicacion(lat: 23.24941, lng: -106.41114,
            origen: OrigenUbicacion.gps, precisionMetros: 8)),
      );
      await tester.tap(find.byKey(const Key('boton_gps_ubicacion')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('±8 m'), findsWidgets);
      await guardar(tester);

      expect(textoQueContiene('Guardada en el teléfono'), findsOneWidget);
      final c = base.db.select(
          "SELECT lat, ubicacion_origen FROM clientes WHERE id = 'cliente-1'").single;
      expect(c['lat'], 23.24941);
      expect(c['ubicacion_origen'], 'gps');
      final tipos = base.db.select('SELECT tipo FROM outbox').map((f) => f['tipo']).toList();
      expect(tipos, contains('cliente.ubicar'));
    });

    testWidgets('a mano: pegada como la copia el mapa, y queda «manual»', (tester) async {
      final base = await abrirPerfil(tester, const GpsSinLectura());
      // Bajo techo el GPS no lee: lo dice y deja escribirla.
      await tester.tap(find.byKey(const Key('boton_gps_ubicacion')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('no leyó a tiempo'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('campo_latitud')), '23.2494, -106.4111');
      await tester.pumpAndSettle();
      expect(
        tester.widget<TextField>(find.byKey(const Key('campo_longitud'))).controller!.text,
        '-106.4111',
      );
      await guardar(tester);
      final c = base.db.select(
          "SELECT lat, lng, ubicacion_origen FROM clientes WHERE id = 'cliente-1'").single;
      expect((c['lat'], c['lng'], c['ubicacion_origen']), (23.2494, -106.4111, 'manual'));
    });

    testWidgets('al revés no se guarda, y se dice por qué', (tester) async {
      final base = await abrirPerfil(tester, const GpsSinLectura());
      await tester.enterText(find.byKey(const Key('campo_latitud')), '-106.41');
      await tester.enterText(find.byKey(const Key('campo_longitud')), '23.24');
      await guardar(tester);
      expect(textoQueContiene('al revés'), findsOneWidget);
      final tipos = base.db.select('SELECT tipo FROM outbox').map((f) => f['tipo']).toList();
      expect(tipos, isNot(contains('cliente.ubicar')));
    });
  });

  group('la oficina', () {
    testWidgets('la corrige desde la ficha del cliente', (tester) async {
      final servidor = _ServidorDeClientes();
      await montarApp(
        tester,
        extras: [
          tokenProvider.overrideWith((_) => 'token-de-prueba'),
          transporteProvider.overrideWithValue(servidor),
          sesionProvider.overrideWith(_Gerencia.new),
          servicioUbicacionProvider
              .overrideWithValue(ServicioUbicacionFalso.siempre(const GpsSinLectura())),
        ],
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('nav_clientes')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cliente_oficina_CLI-1')));
      await tester.pumpAndSettle();
      expect(
        tester.widget<Text>(find.byKey(const Key('ubicacion_cliente_oficina'))).data,
        startsWith('Sin ubicación'),
      );

      await tester.ensureVisible(find.byKey(const Key('campo_latitud')));
      await tester.enterText(find.byKey(const Key('campo_latitud')), '23.2494');
      await tester.enterText(find.byKey(const Key('campo_longitud')), '-106.4111');
      await tester.ensureVisible(find.byKey(const Key('boton_guardar_ubicacion')));
      await tester.tap(find.byKey(const Key('boton_guardar_ubicacion')));
      await tester.pumpAndSettle();

      expect(servidor.posts.single.$1, '/v1/oficina/clientes/cl1/ubicacion');
      expect(servidor.posts.single.$2,
          {'lat': '23.2494000', 'lng': '-106.4111000', 'origen': 'manual'});
      // Lo dice la ficha y lo dice el editor.
      expect(textoQueContiene('Ubicación guardada'), findsWidgets);
      await tester.scrollUntilVisible(
          find.byKey(const Key('ubicacion_cliente_oficina')), -200,
          scrollable: find.byType(Scrollable).first);
      expect(textoQueContiene('23.249400, -106.411100 (a mano)'), findsOneWidget);
    });
  });
}

class _Gerencia extends ControladorSesion {
  @override
  Sesion build() => const SesionDeGerencia(
        Perfil(usuarioId: 'g1', codigo: 'GER01', nombre: 'Bryan', rol: 'gerente',
            permisos: ['tablero.ver', 'ventas.ver_todas', 'clientes.ubicar']),
      );
}

class _ServidorDeClientes implements Transporte {
  final List<(String, Map<String, Object?>)> posts = [];
  String? lat;
  String? lng;

  Map<String, Object?> _ficha({String? mensaje}) => {
        'id': 'cl1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'razon_social': null,
        'contacto': null, 'telefono': null, 'direccion': null, 'referencias': null,
        'ruta': 'Ruta 4', 'estatus': 'activo', 'lat': lat, 'lng': lng,
        'ubicacion_origen': lat == null ? null : 'manual', 'ubicacion_capturada_en': null,
        'comprado_mes': '0.00', 'comprado_anio': '0.00', 'ventas': [],
        'puede_ubicar': true, 'mensaje': mensaje,
      };

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    if (ruta == '/v1/oficina/clientes') {
      return RespuestaHttp(200, jsonEncode({
        'filtro': 'todos', 'recortado': false,
        'conteos': {'todos': 1, 'prospectos': 0, 'sin_ubicacion': 1},
        'clientes': [
          {'id': 'cl1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'ruta': 'Ruta 4',
           'estatus': 'activo', 'telefono': null, 'con_ubicacion': false,
           'ultima_compra': null},
        ],
      }));
    }
    if (ruta == '/v1/oficina/clientes/cl1') return RespuestaHttp(200, jsonEncode(_ficha()));
    return const RespuestaHttp(503, '{"detail":"no importa aquí"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    lat = cuerpo['lat'] as String?;
    lng = cuerpo['lng'] as String?;
    return RespuestaHttp(200, jsonEncode(_ficha(
        mensaje: 'Ubicación guardada. Los teléfonos de su ruta la reciben al sincronizar.')));
  }
}
