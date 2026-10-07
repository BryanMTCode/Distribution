/// El portal de la oficina: lo mismo que el dashboard, en el teléfono.
///
/// Pedidos en operación (octubre 2026): ver el almacén de cada vendedor, sus
/// detalles y sus ventas; los datos por día y por periodo; un resumen de la
/// empresa; y el día de los datos siempre a la vista.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

const _periodos = [
  ['hoy', 'Hoy'], ['ayer', 'Ayer'], ['semana', 'Esta semana'],
  ['semana_pasada', 'Semana pasada'], ['mes', 'Este mes'],
  ['mes_pasado', 'Mes pasado'], ['rango', 'Personalizado'],
];

Map<String, Object?> _periodo(String clave, String desde, String hasta) => {
      'clave': clave, 'etiqueta': clave, 'desde': desde, 'hasta': hasta,
      'descripcion': clave,
    };

/// Un servidor de mentira con lo que piden las pantallas de la oficina.
class _ServidorDeOficina implements Transporte {
  final List<(String, Map<String, String>?)> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    final clave = parametros?['periodo'] ?? 'hoy';
    final (desde, hasta) = switch (clave) {
      'semana' => ('2026-09-21', '2026-09-24'),
      'rango' => (parametros!['desde']!, parametros['hasta']!),
      _ => ('2026-09-24', '2026-09-24'),
    };
    final cuerpo = switch (ruta) {
      '/v1/vendedores' => {
          'periodo': _periodo(clave, desde, hasta),
          'periodos': _periodos,
          'vendedores': [
            {
              'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'activo': true,
              'camion': 'Camión 01', 'rutas': 'R04', 'ventas': 3, 'importe': '2250.00',
              'cobrado': '100.00', 'no_ventas': 1, 'mermas': 0,
              'ultimo_contacto': null, 'saldo_cuenta': '35.00',
            },
          ],
        },
      '/v1/vendedores/v1' => {
          'vendedor': {
            'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'activo': true,
            'camion': 'Camión 01', 'rutas': 'R04 · Centro', 'saldo_cuenta': '35.00',
          },
          'periodo': _periodo(clave, desde, hasta),
          'periodos': _periodos,
          'resumen': [
            {'tipo': 'venta', 'etiqueta': 'Ventas', 'cuantos': 1, 'importe': '2250.00'},
            {'tipo': 'carga', 'etiqueta': 'Cargas', 'cuantos': 1, 'importe': '0.00'},
          ],
          'movimientos': [
            if (parametros?['tipo'] != 'carga')
              {
                'momento': '2026-09-24T16:30:00Z', 'fecha': '2026-09-24', 'tipo': 'venta',
                'etiqueta': 'Ventas', 'folio': 'VEND01-000001', 'cliente': 'La Esquina',
                'detalle': 'De contado', 'importe': '2250.00', 'estado': 'confirmada',
                'marca': false, 'ref': 'venta-1',
              },
            {
              'momento': '2026-09-24T13:00:00Z', 'fecha': '2026-09-24', 'tipo': 'carga',
              'etiqueta': 'Cargas', 'folio': 'CG-000001', 'cliente': null,
              'detalle': 'Carga de 1 producto(s)', 'importe': null,
              'estado': 'confirmada', 'marca': false, 'ref': 'c1',
            },
          ],
          'recortado': false,
          'limite': 500,
          'telefonos': <Object?>[],
        },
      '/v1/vendedores/v1/camion' => {
          'vendedor': 'Juan Pérez', 'camion': 'Camión 01', 'piezas': '55.000',
          'ultimo_contacto': null,
          'existencias': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'cantidad': '60.000'},
            {'producto_id': 'p2', 'sku': 'MARU', 'nombre': 'Maruchan',
             'unidad_base': 'PZA', 'cantidad': '-5.000'},
          ],
        },
      '/v1/vendedores/ventas/venta-1' => {
          'id': 'venta-1', 'folio': 'VEND01-000001', 'fecha_operativa': '2026-09-24',
          'momento': '2026-09-24T16:30:00Z', 'vendedor': 'Juan Pérez',
          'cliente': 'La Esquina', 'tipo': 'contado', 'estado': 'confirmada',
          'total': '2250.00',
          'partidas': [
            {'linea': 1, 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad': 'PZA', 'cantidad': '180.000', 'precio_unitario': '12.50',
             'importe': '2250.00'},
          ],
        },
      '/v1/tablero/periodo' => {
          'periodo': _periodo(clave, desde, hasta),
          'periodos': _periodos,
          'cifras': {
            'contado': '2000.00', 'credito': '250.00', 'total': '2250.00',
            'canceladas': 0, 'cobrado': '100.00', 'mermas': 0, 'devoluciones': 0,
            'no_ventas': 1, 'clientes_atendidos': 2, 'clientes_nuevos': 0,
          },
          'por_vendedor': [
            {'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'ventas': 3,
             'importe': '2250.00', 'contado': '2000.00', 'credito': '250.00',
             'cobrado': '100.00', 'mermas': 0, 'no_ventas': 1},
          ],
          'por_dia': [
            {'fecha': '2026-09-24', 'ventas': 3, 'importe': '2250.00', 'cobrado': '100.00'},
            {'fecha': '2026-09-23', 'ventas': 0, 'importe': '0.00', 'cobrado': '0.00'},
          ],
        },
      '/v1/tablero/empresa' => {
          'clientes_activos': 120, 'prospectos': 4, 'clientes_inactivos': 2,
          'clientes_nuevos_mes': 6, 'clientes_con_saldo': 30, 'vendedores': 5,
          'vendedores_con_camion': 4, 'usuarios_oficina': 3, 'rutas': 5,
          'telefonos': 5, 'productos': 210, 'productos_sin_precio': 1, 'bodegas': 1,
          'camiones': 5, 'piezas_en_bodegas': '4800.000',
          'piezas_en_camiones': '960.000', 'existencias_negativas': 0,
          'cartera': '15000.00', 'cartera_vencida': '0.00',
          'vendido_mes': '98000.00', 'vendido_anio': '980000.00',
        },
      // El tablero del día, de fondo: aquí no importa.
      _ => null,
    };
    if (cuerpo == null) return const RespuestaHttp(503, '{"detail":"sin tablero"}');
    return RespuestaHttp(200, jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      const RespuestaHttp(405, '{}');
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

Future<_ServidorDeOficina> _montar(
  WidgetTester tester, {
  List<String> permisos = const ['tablero.ver', 'ventas.ver_todas', 'inventario.cargar'],
}) async {
  final servidor = _ServidorDeOficina();
  await montarApp(
    tester,
    ahora: DateTime.utc(2026, 9, 24, 19), // jueves 24, 13:00 en Mazatlán
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(servidor),
      sesionProvider.overrideWith(() => _Gerencia(permisos)),
    ],
  );
  await tester.pumpAndSettle();
  return servidor;
}

void main() {
  group('la barra del portal', () {
    testWidgets('la oficina ve Día, Periodo, Empresa, Vendedores y Cargas',
        (tester) async {
      await _montar(tester);
      for (final k in ['nav_dia', 'nav_periodo', 'nav_empresa', 'boton_vendedores',
          'boton_cargas']) {
        expect(find.byKey(Key(k)), findsOneWidget, reason: k);
      }
    });

    testWidgets('sin ventas.ver_todas ni inventario.cargar, esas pestañas no salen',
        (tester) async {
      await _montar(tester, permisos: const ['tablero.ver']);
      expect(find.byKey(const Key('nav_periodo')), findsOneWidget);
      expect(find.byKey(const Key('boton_vendedores')), findsNothing);
      expect(find.byKey(const Key('boton_cargas')), findsNothing);
    });

    testWidgets('abrir la app no consulta las pestañas que no se han abierto',
        (tester) async {
      final servidor = await _montar(tester);
      expect(servidor.pedidas.map((p) => p.$1), isNot(contains('/v1/vendedores')));
      expect(servidor.pedidas.map((p) => p.$1), isNot(contains('/v1/tablero/empresa')));
    });
  });

  group('vendedores', () {
    testWidgets('la lista dice de qué día son las cifras', (tester) async {
      await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_vendedores')), findsOneWidget);
      expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
      expect(textoQueContiene(r'3 venta(s) · $2,250.00'), findsOneWidget);
      expect(textoQueContiene(r'Debe $35.00'), findsOneWidget);
    });

    testWidgets('cambiar a la semana pide la semana y lo dice con fechas', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('periodo_semana')).first);
      await tester.pumpAndSettle();

      expect(servidor.pedidas.last.$2, {'periodo': 'semana'});
      expect(find.text('Del lunes 21 de septiembre al jueves 24 de septiembre'),
          findsOneWidget);
    });

    testWidgets('su ficha: movimientos, filtro, venta abierta y su camión',
        (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('vendedor_VEND01')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_vendedor')), findsOneWidget);
      expect(find.text('Debe en su cuenta: \$35.00'), findsOneWidget);
      expect(find.byKey(const Key('movimiento_venta_VEND01-000001')), findsOneWidget);
      expect(find.byKey(const Key('movimiento_carga_CG-000001')), findsOneWidget);

      // El resumen es el filtro.
      await tester.tap(find.byKey(const Key('tipo_carga')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$2, {'periodo': 'hoy', 'tipo': 'carga'});
      expect(find.byKey(const Key('movimiento_venta_VEND01-000001')), findsNothing);

      await tester.tap(find.byKey(const Key('tipo_todos')));
      await tester.pumpAndSettle();
      // La venta se abre con lo que se le vendió a la tienda.
      await tester.tap(find.byKey(const Key('movimiento_venta_VEND01-000001')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_venta_vista')), findsOneWidget);
      expect(find.text('Atún en agua 140 g'), findsOneWidget);
      expect(find.text('180 PZA × \$12.50'), findsOneWidget);
      expect(find.byKey(const Key('total_venta_vista')), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();

      // Su camión, con el negativo marcado.
      await tester.tap(find.byKey(const Key('pestana_camion')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('camion_ATUN-140')), findsOneWidget);
      expect(find.text('60 PZA'), findsOneWidget);
      expect(textoQueContiene('En negativo'), findsOneWidget);
    });
  });

  group('periodo y empresa', () {
    testWidgets('el periodo trae lo vendido, por vendedor y por día', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_periodo')));
      await tester.pumpAndSettle();

      expect(servidor.pedidas.last.$1, '/v1/tablero/periodo');
      expect(servidor.pedidas.last.$2, {'periodo': 'semana'});
      expect(find.text('Del lunes 21 de septiembre al jueves 24 de septiembre'),
          findsOneWidget);
      expect(find.byKey(const Key('periodo_vendido')), findsOneWidget);
      expect(find.byKey(const Key('periodo_vendedor_VEND01')), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const Key('periodo_dia_2026-09-23')),
        200,
        scrollable: find.descendant(
          of: find.byKey(const Key('lista_periodo')),
          matching: find.byType(Scrollable),
        ).first,
      );
      expect(find.text('miércoles 23 de septiembre'), findsOneWidget);
    });

    testWidgets('tocar un día abre el tablero de ese día', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_periodo')));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.byKey(const Key('periodo_dia_2026-09-23')),
        200,
        scrollable: find.descendant(
          of: find.byKey(const Key('lista_periodo')),
          matching: find.byType(Scrollable),
        ).first,
      );
      await tester.tap(find.byKey(const Key('periodo_dia_2026-09-23')));
      await tester.pumpAndSettle();

      expect(servidor.pedidas.last.$1, '/v1/tablero');
      expect(servidor.pedidas.last.$2, {'fecha': '2026-09-23'});
      expect(find.byKey(const Key('boton_volver_a_hoy')), findsOneWidget);
    });

    testWidgets('la empresa: clientes, vendedores, artículos', (tester) async {
      await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_empresa')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_empresa')), findsOneWidget);
      expect(find.descendant(of: find.byKey(const Key('empresa_clientes')),
          matching: find.text('120')), findsOneWidget);
      expect(find.descendant(of: find.byKey(const Key('empresa_vendedores')),
          matching: find.text('5')), findsOneWidget);
      expect(find.descendant(of: find.byKey(const Key('empresa_articulos')),
          matching: find.text('210')), findsOneWidget);
    });
  });

  group('el día a la vista', () {
    testWidgets('Mi día dice qué día es', (tester) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        ahora: DateTime.utc(2026, 9, 24, 19),
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_mi_dia')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('dia_de_mi_dia')), findsOneWidget);
      expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
    });

    testWidgets('la entrada lleva el logo de Distribuciones SE', (tester) async {
      await montarApp(tester, credencial: credencialDelServidor());
      expect(find.byKey(const Key('logo_distribuciones_se')), findsOneWidget);
      expect(find.text('Distribuciones'), findsOneWidget);
      expect(find.text('SE'), findsOneWidget);
    });
  });
}
