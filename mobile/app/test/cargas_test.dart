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
/// El renglón de la lista de una carga: `dia` es el día en que se hizo, y se
/// vende ese mismo día salvo que diga otro (ADR 0002 §151).
Map<String, Object?> _resumen(
  String folio,
  String dia,
  String estado,
  String piezas, {
  String? vende,
}) => {
      'id': folio, 'folio': folio, 'estado': estado, 'fecha_operativa': vende ?? dia,
      'hecha_el': dia,
      'vendedor': 'Juan Pérez', 'camion': 'Camión 01', 'renglones': 1, 'piezas': piezas,
    };

class _ServidorDeCargas implements Transporte {
  _ServidorDeCargas({
    this.bloqueos = const [],
    bool yaConfirmada = false,
    this.vivas = const [],
  }) : _estado = yaConfirmada ? 'confirmada' : 'borrador';

  final List<String> bloqueos;

  /// Cargas vivas de otros días, además de la que se abre en la prueba.
  final List<Map<String, Object?>> vivas;
  final List<(String, Map<String, Object?>)> posts = [];

  /// Lo que se pidió por día: el día en que se hicieron (ADR 0002 §151).
  final List<Map<String, String>> porFecha = [];
  final List<Map<String, Object?>> _renglones = [];
  String _estado;
  bool _abierta = false;

  Map<String, Object?> get _detalle => {
        'id': 'c1',
        'folio': 'CG-000007',
        'estado': _estado,
        // Se abre hoy para mañana (ADR 0002 §82).
        'fecha_operativa': '2026-09-25',
        'hecha_el': '2026-09-24',
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
    final todas = [
      _resumen('CG-000006', '2026-09-23', 'liquidada', '120.000'),
      _resumen('CG-000005', '2026-09-23', 'liquidada', '80.000'),
      _resumen('CG-000004', '2026-09-22', 'cancelada', '10.000'),
      _resumen('CG-000003', '2026-09-21', 'liquidada', '40.000'),
      ...vivas,
      if (_abierta)
        {
          'id': 'c1', 'folio': 'CG-000007', 'estado': _estado,
          'fecha_operativa': '2026-09-25', 'hecha_el': '2026-09-24',
          'vendedor': 'Juan Pérez',
          'camion': 'Camión 01', 'renglones': _renglones.length, 'piezas': '0.000',
        },
    ];
    // Un día: las que se hicieron ese día, como lo contesta el servidor.
    if (ruta == '/v1/cargas' && parametros != null && parametros.isNotEmpty) {
      porFecha.add(parametros);
      return RespuestaHttp(200, jsonEncode([
        for (final c in todas)
          if (c['hecha_el'] == parametros['hechas_el']) c,
      ]));
    }
    // Sin parámetros: las vivas de cualquier día.
    if (ruta == '/v1/cargas') {
      return RespuestaHttp(200, jsonEncode([
        for (final c in todas)
          if (const ['borrador', 'confirmada', 'en_ruta'].contains(c['estado'])) c,
      ]));
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
  // Cargas vive en la pestaña Almacén; con solo `inventario.cargar` es la única
  // parte y se muestra directo, sin pestañas. Arriba van las que pidieron los
  // vendedores (ADR 0002 §82).
  await tester.tap(find.byKey(const Key('nav_almacen')));
  await tester.pumpAndSettle();
  expect(find.byKey(const Key('pestana_cargas')), findsNothing);
  expect(find.byKey(const Key('seccion_cargas_pedidas')), findsOneWidget);
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
  testWidgets('las cargas van por día, se pasa de día en día y cada una dice cómo va (§149)',
      (tester) async {
    // «Quiero que aparezcan siempre por día y me pueda mover por días, y que
    // aparezca el estatus de cada carga.»
    final servidor = _ServidorDeCargas(vivas: [
      _resumen('CG-000008', '2026-09-24', 'confirmada', '240.000', vende: '2026-09-25'),
      _resumen('CG-000002', '2026-09-20', 'en_ruta', '60.000'),
    ]);
    await _montar(tester, servidor);
    await tester.tap(find.byKey(const Key('nav_almacen')));
    await tester.pumpAndSettle();

    // De entrada, hoy: las que se hicieron hoy.
    expect(servidor.porFecha.last, {'hechas_el': '2026-09-24'});
    expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
    expect(textoQueContiene('1 carga(s) · 240 piezas · 1 en el camión'), findsOneWidget);
    expect(
      find.descendant(
        of: find.byKey(const Key('estado_carga_CG-000008')),
        matching: find.text('En el camión'),
      ),
      findsOneWidget,
    );
    // Se hizo hoy y se vende mañana: lo dice (§151).
    expect(textoQueContiene('para vender el viernes 25 de septiembre'), findsOneWidget);
    // La que sigue viva de otro día, a la mano: es la que estorba para cargar.
    expect(find.byKey(const Key('sin_cerrar_2026-09-20')), findsOneWidget);
    // Hoy es el último día: una carga no se hace en un día que no ha llegado.
    expect(
      tester.widget<IconButton>(find.byKey(const Key('boton_dia_siguiente_cargas'))).onPressed,
      isNull,
    );

    // Un día atrás: las dos liquidadas; la cancelada no cuenta y va aparte.
    await tester.tap(find.byKey(const Key('boton_dia_anterior_cargas')));
    await tester.pumpAndSettle();
    expect(servidor.porFecha.last, {'hechas_el': '2026-09-23'});
    expect(find.text('Ayer, miércoles 23 de septiembre'), findsOneWidget);
    expect(textoQueContiene('2 carga(s) · 200 piezas · 2 liquidadas'), findsOneWidget);
    expect(find.text('Liquidada'), findsNWidgets(2));
    expect(find.byKey(const Key('carga_CG-000008')), findsNothing);

    // Dos atrás, ninguna viva: la cancelada va aparte y no cuenta.
    await tester.tap(find.byKey(const Key('boton_dia_anterior_cargas')));
    await tester.pumpAndSettle();
    expect(servidor.porFecha.last, {'hechas_el': '2026-09-22'});
    expect(find.byKey(const Key('cargas_vacio')), findsOneWidget);

    // La de otro día sin cerrar lleva a su día.
    await tester.tap(find.byKey(const Key('sin_cerrar_2026-09-20')));
    await tester.pumpAndSettle();
    expect(servidor.porFecha.last, {'hechas_el': '2026-09-20'});
    expect(find.text('En el corte'), findsOneWidget);

    await tester.tap(find.byKey(const Key('boton_cargas_de_hoy')));
    await tester.pumpAndSettle();
    expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
    expect(find.byKey(const Key('boton_cargas_de_hoy')), findsNothing);
  });

  testWidgets('la de anoche es de anoche: en la mañana abre en ayer, que es lo que se atiende',
      (tester) async {
    // «Si la hice el lunes en la noche, tiene que quedar el lunes, no el
    // martes» (ADR 0002 §151). Hoy no se ha hecho ninguna.
    final servidor = _ServidorDeCargas(vivas: [
      _resumen('CG-000009', '2026-09-23', 'confirmada', '240.000', vende: '2026-09-24'),
    ]);
    await _montar(tester, servidor);
    await tester.tap(find.byKey(const Key('nav_almacen')));
    await tester.pumpAndSettle();

    expect(servidor.porFecha.single, {'hechas_el': '2026-09-23'});
    expect(find.text('Ayer, miércoles 23 de septiembre'), findsOneWidget);
    expect(find.byKey(const Key('carga_CG-000009')), findsOneWidget);
    expect(textoQueContiene('para vender el jueves 24 de septiembre'), findsOneWidget);
    expect(find.byKey(const Key('sin_cerrar_2026-09-23')), findsNothing);
  });

  testWidgets('sin el permiso de cargar, el botón ni aparece', (tester) async {
    await _montar(tester, _ServidorDeCargas(), permisos: const ['tablero.ver']);
    expect(find.byKey(const Key('panel_gerencia')), findsOneWidget);
    expect(find.byKey(const Key('nav_almacen')), findsNothing);
  });

  testWidgets('abrir, capturar cajas y confirmar', (tester) async {
    final servidor = _ServidorDeCargas();
    await _montar(tester, servidor);
    await _abrirCarga(tester);

    expect(find.byKey(const Key('pantalla_detalle_carga')), findsOneWidget);
    // Para mañana, que es lo normal (§82).
    expect(servidor.posts.first.$2,
        {'vendedor_id': 'v1', 'almacen_origen_id': 'b1', 'fecha_operativa': '2026-09-25'});
    // Se hizo hoy, para vender mañana (§151).
    expect(
      tester.widget<Text>(find.byKey(const Key('dia_de_la_carga'))).data,
      'Sale de Bodega · se hizo el jueves 24 de septiembre · para vender el viernes 25 de '
      'septiembre',
    );
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

  testWidgets('la carga para hoy: el camión que sale a vender ese mismo día (§157)',
      (tester) async {
    // Con la fecha de mañana, el corte de esta noche no vería las ventas de hoy.
    final servidor = _ServidorDeCargas();
    await _montar(tester, servidor);
    await tester.tap(find.byKey(const Key('nav_almacen')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('boton_nueva_carga')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('Para vender mañana, viernes 25 de septiembre'), findsOneWidget);
    expect(textoQueContiene('Cortes y cargas'), findsNothing);
    await tester.tap(find.byKey(const Key('carga_para_hoy')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('Para vender hoy, jueves 24 de septiembre'), findsOneWidget);
    await tester.tap(find.byKey(const Key('campo_vendedor_carga')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Juan Pérez · Camión 01').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('boton_abrir_carga')));
    await tester.pumpAndSettle();
    expect(servidor.posts.first.$2,
        {'vendedor_id': 'v1', 'almacen_origen_id': 'b1', 'fecha_operativa': '2026-09-24'});
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
