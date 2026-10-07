/// Cargar el camión desde el portal de la oficina.
///
/// Pedido en operación (octubre 2026): «las cargas quiero hacerlas también en la
/// app, pero solo para los administradores y puestos de arriba, no para los
/// vendedores».
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Un servidor de cargas de mentira, con lo justo para recorrer el flujo.
class _ServidorDeCargas implements Transporte {
  _ServidorDeCargas({this.bloqueos = const [], bool yaConfirmada = false})
      : _estado = yaConfirmada ? 'confirmada' : 'borrador';

  final List<String> bloqueos;
  final List<(String, Map<String, Object?>)> posts = [];
  final List<Map<String, Object?>> _renglones = [];
  String _estado;
  bool _abierta = false;

  Map<String, Object?> get _detalle => {
        'id': 'c1',
        'folio': 'CG-000007',
        'estado': _estado,
        'fecha_operativa': '2026-09-24',
        'vendedor': 'Juan Pérez',
        'camion': 'Camión 01',
        'bodega': 'Bodega',
        'editable': _estado == 'borrador',
        'renglones': _renglones,
        'surtido': _estado != 'borrador'
            ? <Object?>[]
            : [
                {
                  'producto_id': 'p1',
                  'sku': 'ATUN-140',
                  'nombre': 'Atún en agua 140 g',
                  'unidad_base': 'PZA',
                  'en_bodega': '480.000',
                  'ya_en_la_carga': '0.000',
                  'presentaciones': [
                    {'unidad': 'CAJA', 'factor': '24.000'},
                    {'unidad': 'PZA', 'factor': '1.000'},
                  ],
                  'por_omision': 'CAJA',
                },
              ],
        'bloqueos': _estado == 'borrador' ? bloqueos : <String>[],
        'mensaje': _estado == 'confirmada' ? 'Carga CG-000007 confirmada.' : null,
      };

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    if (ruta == '/v1/cargas/opciones') {
      return RespuestaHttp(200, jsonEncode({
        'vendedores': [
          {'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'camion': 'Camión 01'},
        ],
        'bodegas': [
          {'id': 'b1', 'codigo': 'BODEGA', 'nombre': 'Bodega'},
        ],
        'hoy': '2026-09-24',
      }));
    }
    if (ruta == '/v1/cargas') {
      return RespuestaHttp(200, jsonEncode(_abierta
          ? [
              {
                'id': 'c1', 'folio': 'CG-000007', 'estado': _estado,
                'fecha_operativa': '2026-09-24', 'vendedor': 'Juan Pérez',
                'camion': 'Camión 01', 'renglones': _renglones.length, 'piezas': '0.000',
              },
            ]
          : <Object?>[]));
    }
    if (ruta == '/v1/cargas/c1') return RespuestaHttp(200, jsonEncode(_detalle));
    // El tablero de fondo: no importa aquí.
    return const RespuestaHttp(503, '{"detail":"sin tablero en esta prueba"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    switch (ruta) {
      case '/v1/cargas':
        _abierta = true;
      case '/v1/cargas/c1/renglones':
        final pedido = (cuerpo['renglones']! as List).single as Map;
        final cajas = int.parse(pedido['cantidad'] as String);
        _renglones.add({
          'id': 'r1', 'producto_id': 'p1', 'sku': 'ATUN-140',
          'nombre': 'Atún en agua 140 g', 'unidad_base': 'PZA',
          'cantidad': '${cajas * 24}.000', 'en_bodega': '480.000',
        });
      case '/v1/cargas/c1/confirmar':
        if (bloqueos.isNotEmpty && cuerpo['motivo_forzado'] == null) {
          return const RespuestaHttp(409, '{"detail":"No se puede confirmar: pendientes."}');
        }
        _estado = 'confirmada';
    }
    return RespuestaHttp(200, jsonEncode(_detalle));
  }
}

class _Gerencia extends ControladorSesion {
  _Gerencia(this.permisos);

  final List<String> permisos;

  @override
  Sesion build() => SesionDeGerencia(
        Perfil(usuarioId: 'g1', codigo: 'GER01', nombre: 'Bryan', rol: 'gerente',
            permisos: permisos),
      );
}

Future<void> _montar(
  WidgetTester tester,
  Transporte transporte, {
  List<String> permisos = const ['tablero.ver', 'inventario.cargar'],
}) async {
  await montarApp(
    tester,
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(transporte),
      sesionProvider.overrideWith(() => _Gerencia(permisos)),
    ],
  );
  await tester.pumpAndSettle();
}

Future<void> _abrirCarga(WidgetTester tester) async {
  // Cargas vive en la pestaña Camiones; con solo `inventario.cargar`, directo.
  await tester.tap(find.byKey(const Key('nav_camiones')));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('cargas_vacio')), findsOneWidget);

  await tester.tap(find.byKey(const Key('boton_nueva_carga')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('campo_vendedor_carga')));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Juan Pérez · Camión 01').last);
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('boton_abrir_carga')));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('sin el permiso de cargar, el botón ni aparece', (tester) async {
    await _montar(tester, _ServidorDeCargas(), permisos: const ['tablero.ver']);
    expect(find.byKey(const Key('panel_gerencia')), findsOneWidget);
    expect(find.byKey(const Key('nav_camiones')), findsNothing);
  });

  testWidgets('abrir, capturar cajas y confirmar', (tester) async {
    final servidor = _ServidorDeCargas();
    await _montar(tester, servidor);
    await _abrirCarga(tester);

    expect(find.byKey(const Key('pantalla_detalle_carga')), findsOneWidget);
    expect(servidor.posts.first.$2, {'vendedor_id': 'v1', 'almacen_origen_id': 'b1'});
    // La bodega ofrece el producto con la caja ya elegida: en la bodega se
    // carga por caja.
    expect(find.text('Hay 480 PZA'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('cantidad_carga_ATUN-140')), '10');
    await tester.tap(find.byKey(const Key('boton_agregar_a_la_carga')));
    await tester.pumpAndSettle();

    expect(servidor.posts.last.$2, {
      'renglones': [
        {'producto_id': 'p1', 'unidad': 'CAJA', 'cantidad': '10'},
      ],
    });
    expect(
      find.descendant(
        of: find.byKey(const Key('renglon_carga_ATUN-140')),
        matching: find.text('240 PZA'),
      ),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const Key('boton_confirmar_carga')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('boton_confirmar_de_verdad')));
    await tester.pumpAndSettle();

    expect(servidor.posts.last.$1, '/v1/cargas/c1/confirmar');
    expect(find.text('Carga CG-000007 confirmada.'), findsOneWidget);
    // Confirmada ya no se edita: sin botones de captura.
    expect(find.byKey(const Key('boton_confirmar_carga')), findsNothing);
  });

  testWidgets('con pendientes, confirmar pide el motivo y lo manda',
      (tester) async {
    final servidor = _ServidorDeCargas(
      bloqueos: const ['«Moto G54» reportó 3 operación(es) sin subir.'],
    );
    await _montar(tester, servidor);
    await _abrirCarga(tester);

    // El bloqueo se ve desde que se abre, no al final.
    expect(textoQueContiene('reportó 3 operación(es)'), findsWidgets);

    await tester.enterText(find.byKey(const Key('cantidad_carga_ATUN-140')), '1');
    await tester.tap(find.byKey(const Key('boton_agregar_a_la_carga')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('boton_confirmar_carga')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('campo_motivo_forzado')),
      'Juan se quedó sin batería',
    );
    await tester.tap(find.byKey(const Key('boton_confirmar_de_verdad')));
    await tester.pumpAndSettle();

    expect(servidor.posts.last.$2, {'motivo_forzado': 'Juan se quedó sin batería'});
    expect(find.text('Carga CG-000007 confirmada.'), findsOneWidget);
  });

  testWidgets('sin motivo, el servidor dice que no y la pantalla lo muestra',
      (tester) async {
    final servidor = _ServidorDeCargas(bloqueos: const ['hay pendientes']);
    await _montar(tester, servidor);
    await _abrirCarga(tester);
    await tester.enterText(find.byKey(const Key('cantidad_carga_ATUN-140')), '1');
    await tester.tap(find.byKey(const Key('boton_agregar_a_la_carga')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('boton_confirmar_carga')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('boton_confirmar_de_verdad')));
    await tester.pumpAndSettle();

    expect(find.text('No se puede confirmar: pendientes.'), findsOneWidget);
    expect(find.byKey(const Key('boton_confirmar_carga')), findsOneWidget);
  });

  testWidgets('si el vendedor ya tiene su carga del día confirmada, se explica',
      (tester) async {
    // Reportado en operación: «en la app no me deja cargar al vendedor». Cada
    // vendedor lleva UNA carga por día; abrir otra devuelve la ya confirmada, y
    // la pantalla quedaba sin botones y sin una razón.
    final servidor = _ServidorDeCargas(yaConfirmada: true);
    await _montar(tester, servidor);
    await _abrirCarga(tester);

    expect(find.byKey(const Key('aviso_carga_ya_confirmada')), findsOneWidget);
    expect(textoQueContiene('UNA carga por día'), findsOneWidget);
    expect(find.byKey(const Key('boton_confirmar_carga')), findsNothing);
  });
}
