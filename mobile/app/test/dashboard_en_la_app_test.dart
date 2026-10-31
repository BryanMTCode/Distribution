/// Las pantallas del dashboard dentro de la app de la oficina (ADR 0002 §194):
/// los botones que manda el servidor, en Almacén, y la pantalla que los abre.
///
/// La vista web en sí no corre en una prueba (no hay Android); se prueba lo que
/// la rodea: qué botones salen y a quién, qué se hace con cada enlace que se
/// toca dentro del dashboard, y qué se dice cuando no se puede abrir.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/dashboard.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_app/src/pantallas/gerencia/dashboard.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

const _atajos = [
  {'titulo': 'Mover mercancía', 'descripcion': 'De la bodega a un camión',
   'ruta': '/panel/inventario/mover', 'icono': 'mover'},
  {'titulo': 'Artículos', 'descripcion': 'Dar de alta productos',
   'ruta': '/panel/productos', 'icono': 'articulos'},
];

/// Qué contesta el servidor cuando la app pide el boleto.
enum _Boleto { sinSenal, sinPermiso }

/// Un servidor de mentira: los botones, y un boleto que no se puede dar. Con un
/// boleto bueno la pantalla abriría la vista web, que en una prueba no existe.
class _Servidor implements Transporte {
  _Servidor({this.atajos = _atajos, this.boleto = _Boleto.sinSenal});

  /// `null`: un servidor que todavía no tiene `/v1/panel`.
  final Object? atajos;
  final _Boleto boleto;
  final List<String> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add('GET $ruta');
    if (ruta == '/v1/panel/atajos' && atajos != null) {
      return RespuestaHttp(200, jsonEncode(atajos));
    }
    if (ruta == '/v1/almacen') return const RespuestaHttp(200, '[]');
    return const RespuestaHttp(404, '{"detail":"Not Found"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    pedidas.add('POST $ruta');
    if (ruta != '/v1/panel/boleto') return const RespuestaHttp(405, '{}');
    return switch (boleto) {
      _Boleto.sinSenal => throw const ErrorDeRed('sin señal'),
      _Boleto.sinPermiso => const RespuestaHttp(403, '{"detail":"no"}'),
    };
  }
}

class _Oficina extends ControladorSesion {
  _Oficina(this.rol);

  final String rol;

  @override
  Sesion build() => SesionDeGerencia(
        Perfil(usuarioId: 'a1', codigo: 'ADMIN01', nombre: 'Bryan', rol: rol,
            permisos: const [
              'tablero.ver', 'inventario.ver', 'inventario.ajustar',
              'inventario.cargar', 'inventario.liquidar', 'mayoreo.vender',
            ]),
      );
}

Future<_Servidor> _montar(WidgetTester tester, {_Servidor? servidor, String rol = 'admin'}) async {
  final s = servidor ?? _Servidor();
  await montarApp(
    tester,
    ahora: DateTime.utc(2026, 10, 31, 19),
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(s),
      sesionProvider.overrideWith(() => _Oficina(rol)),
    ],
  );
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('nav_almacen')));
  await tester.pumpAndSettle();
  return s;
}

Future<void> _irAMoverYCapturar(WidgetTester tester) async {
  await tester.ensureVisible(find.byKey(const Key('pestana_mover_y_capturar')));
  await tester.tap(find.byKey(const Key('pestana_mover_y_capturar')));
  await tester.pumpAndSettle();
}

void main() {
  group('a dónde va cada enlace del dashboard', () {
    final servidor = Uri.parse('https://api.distribucionesse.com');
    Destino de(String url) => adondeVa(Uri.parse(url), servidor);

    test('las pantallas del dashboard se abren ahí mismo', () {
      expect(de('https://api.distribucionesse.com/panel'), Destino.dentro);
      expect(de('https://api.distribucionesse.com/panel/inventario/mover'), Destino.dentro);
      expect(de('https://api.distribucionesse.com/panel/productos?q=atun'), Destino.dentro);
    });

    test('el Excel, al navegador del teléfono; «Salir», de regreso a la app', () {
      expect(de('https://api.distribucionesse.com/panel/reporte/excel?desde=2026-10-01'),
          Destino.descarga);
      expect(de('https://api.distribucionesse.com/panel/salir'), Destino.salir);
    });

    test('lo que no es el dashboard de este servidor sale al navegador', () {
      expect(de('https://otro.com/panel/productos'), Destino.afuera);
      expect(de('http://api.distribucionesse.com/panel'), Destino.afuera);
      expect(de('https://api.distribucionesse.com:8443/panel'), Destino.afuera);
      expect(de('https://api.distribucionesse.com/v1/sync/pull'), Destino.afuera);
      expect(de('https://api.distribucionesse.com/panelito'), Destino.afuera);
    });

    test('el Excel lleva su propio boleto, porque el navegador no tiene la sesión', () {
      final url = enElNavegador(
        servidor,
        const BoletoDelPanel(boleto: 'a/b+c=', entrar: '/panel/entrar/app'),
        Uri.parse('https://api.distribucionesse.com/panel/reporte/excel'
            '?desde=2026-10-01&hasta=2026-10-31'),
      );
      expect((url.scheme, url.host, url.path),
          ('https', 'api.distribucionesse.com', '/panel/entrar/app'));
      expect(url.queryParameters, {
        'boleto': 'a/b+c=',
        'siguiente': '/panel/reporte/excel?desde=2026-10-01&hasta=2026-10-31',
      });
    });
  });

  group('Mover y capturar, en Almacén', () {
    testWidgets('los botones que manda el servidor, al final de las pestañas',
        (tester) async {
      final s = await _montar(tester);
      expect(s.pedidas, contains('GET /v1/panel/atajos'));
      final pestanas = tester.widget<TabBar>(find.byType(TabBar)).tabs;
      expect(pestanas.map((t) => t.key), [
        const Key('pestana_existencias'),
        const Key('pestana_cargas'),
        const Key('pestana_cortes'),
        const Key('pestana_mover_y_capturar'),
      ]);

      await _irAMoverYCapturar(tester);
      expect(find.byKey(const Key('atajos_del_panel')), findsOneWidget);
      expect(find.byKey(const Key('atajo_/panel/inventario/mover')), findsOneWidget);
      expect(find.byKey(const Key('atajo_/panel/productos')), findsOneWidget);
      expect(find.text('Mover mercancía'), findsOneWidget);
      expect(find.text('Dar de alta productos'), findsOneWidget);
    });

    testWidgets('a quien el servidor no le da botones, la parte no aparece',
        (tester) async {
      await _montar(tester, servidor: _Servidor(atajos: const []), rol: 'gerente');
      expect(find.byKey(const Key('pestana_existencias')), findsOneWidget);
      expect(find.byKey(const Key('pestana_mover_y_capturar')), findsNothing);
    });

    testWidgets('con un servidor que todavía no los tiene, tampoco', (tester) async {
      await _montar(tester, servidor: _Servidor(atajos: null));
      expect(find.byKey(const Key('pestana_existencias')), findsOneWidget);
      expect(find.byKey(const Key('pestana_mover_y_capturar')), findsNothing);
    });

    testWidgets('la app del vendedor ni los pide ni los muestra', (tester) async {
      final s = _Servidor();
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        extras: [
          transporteProvider.overrideWithValue(s),
          tokenProvider.overrideWith((_) => 'token-de-prueba'),
        ],
      );
      await entrarCon(tester, pinCorrecto);
      expect(find.byKey(const Key('boton_sincronizar')), findsOneWidget);
      expect(find.byKey(const Key('nav_almacen')), findsNothing);
      expect(s.pedidas.where((p) => p.contains('/v1/panel')), isEmpty);
    });
  });

  group('la pantalla del dashboard', () {
    testWidgets('sin señal lo dice, y se puede volver a intentar', (tester) async {
      final s = await _montar(tester);
      await _irAMoverYCapturar(tester);
      await tester.tap(find.byKey(const Key('atajo_/panel/inventario/mover')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_del_dashboard')), findsOneWidget);
      expect(find.text('Mover mercancía'), findsOneWidget); // el título
      expect(tester.widget<Text>(find.byKey(const Key('error_del_dashboard'))).data,
          contains('Sin señal'));
      expect(s.pedidas.where((p) => p == 'POST /v1/panel/boleto'), hasLength(1));

      await tester.tap(find.byKey(const Key('reintentar_dashboard')));
      await tester.pumpAndSettle();
      expect(s.pedidas.where((p) => p == 'POST /v1/panel/boleto'), hasLength(2));
      expect(find.byKey(const Key('error_del_dashboard')), findsOneWidget);

      // Atrás, de regreso a los botones.
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_del_dashboard')), findsNothing);
      expect(find.byKey(const Key('atajos_del_panel')), findsOneWidget);
    });

    testWidgets('si el servidor no le da el boleto, dice por qué', (tester) async {
      await _montar(tester, servidor: _Servidor(boleto: _Boleto.sinPermiso));
      await _irAMoverYCapturar(tester);
      await tester.tap(find.byKey(const Key('atajo_/panel/productos')));
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.byKey(const Key('error_del_dashboard'))).data,
          'Tu usuario no abre el dashboard desde la app.');
    });

    testWidgets('sin sesión en la app no pide nada', (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [transporteProvider.overrideWithValue(null)],
          child: const MaterialApp(
            home: PantallaDelDashboard(titulo: 'Artículos', ruta: '/panel/productos'),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.byKey(const Key('error_del_dashboard'))).data,
          contains('Tu sesión venció'));
    });
  });
}
