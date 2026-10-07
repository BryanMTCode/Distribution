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
      '/v1/oficina/clientes' => {
          'filtro': parametros?['filtro'] ?? 'todos',
          'recortado': false,
          'conteos': {'todos': 2, 'con_saldo': 1, 'vencidos': 1, 'bloqueados': 0,
                      'prospectos': 0},
          'clientes': [
            {'id': 'cl1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'ruta': 'Ruta 4',
             'estatus': 'activo', 'bloqueado': false, 'telefono': null,
             'saldo': '400.00', 'saldo_vencido': '400.00', 'facturas_vencidas': 1,
             'ultima_compra': '2026-09-24'},
            if (parametros?['filtro'] != 'vencidos')
              {'id': 'cl2', 'codigo': 'CLI-2', 'nombre': 'Al Corriente', 'ruta': 'Ruta 4',
               'estatus': 'activo', 'bloqueado': false, 'telefono': null,
               'saldo': '0.00', 'saldo_vencido': '0.00', 'facturas_vencidas': 0,
               'ultima_compra': null},
          ],
        },
      '/v1/oficina/clientes/cl1' => _fichaCliente(),
      '/v1/cortes' => {
          'por_cortar': [
            {'carga_id': 'cg1', 'folio': 'CG-000001', 'fecha_operativa': '2026-09-24',
             'vendedor': 'Juan Pérez', 'camion': 'Camión 01', 'dias': 0, 'ventas': 3,
             'importe': '2250.00'},
          ],
          'cortes': <Object?>[],
        },
      // El tablero del día, de fondo: aquí no importa.
      _ => null,
    };
    if (cuerpo == null) return const RespuestaHttp(503, '{"detail":"sin tablero"}');
    return RespuestaHttp(200, jsonEncode(cuerpo));
  }

  final List<(String, Map<String, Object?>)> posts = [];
  bool bloqueado = false;
  String contada = '0.000';
  bool cerrado = false;

  Map<String, Object?> _fichaCliente({String? mensaje}) => {
        'id': 'cl1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'razon_social': null,
        'contacto': 'Doña Mary', 'telefono': '6691234567', 'direccion': 'Juárez 10',
        'ruta': 'Ruta 4', 'estatus': 'activo', 'permite_credito': true,
        'limite_credito': '5000.00', 'dias_credito': 7, 'bloqueado': bloqueado,
        'bloqueo_motivo': bloqueado ? 'Debe tres notas' : null,
        'saldo': '400.00', 'disponible': '4600.00', 'saldo_vencido': '400.00',
        'por_confirmar': '0.00', 'comprado_mes': '2750.00', 'comprado_anio': '2750.00',
        'cuentas': [
          {'venta_id': 'venta-1', 'folio': 'VEND01-000009', 'fecha_emision': '2026-09-04',
           'fecha_vencimiento': '2026-09-11', 'importe_original': '500.00',
           'importe_pagado': '100.00', 'saldo': '400.00', 'vencida': true,
           'dias_vencida': 13},
        ],
        'ventas': [
          {'id': 'venta-1', 'folio': 'VEND01-000001', 'fecha': '2026-09-24',
           'momento': null, 'tipo': 'contado', 'estado': 'confirmada',
           'total': '2250.00', 'vendedor': 'Juan Pérez'},
        ],
        'cobros': <Object?>[],
        'puede_bloquear': true,
        'mensaje': mensaje,
      };

  Map<String, Object?> _corte({String? mensaje}) => {
        'id': 'lq1', 'folio': 'LQ-000001', 'estado': cerrado ? 'cerrada' : 'abierta',
        'abierto': !cerrado, 'fecha_operativa': '2026-09-24', 'vendedor': 'Juan Pérez',
        'camion': 'Camión 01', 'carga': 'CG-000001', 'efectivo_esperado': '2250.00',
        'efectivo_entregado': '0.00', 'diferencia_efectivo': '-2250.00',
        'arqueo_hecho': false, 'observaciones': null,
        'renglones': [
          {'id': 'r1', 'sku': 'ATUN-140', 'nombre': 'Atún', 'unidad_base': 'PZA',
           'inicial': '0.000', 'cargada': '240.000', 'vendida': '180.000',
           'merma': '0.000', 'devuelta': '0.000', 'esperado': '60.000',
           'contada': contada,
           'diferencia': contada == '0.000' ? '-60.000' : '0.000',
           'presentaciones': <Object?>[]},
        ],
        'bloqueos': <String>[],
        'respaldo': {'respaldado': false, 'motivo': 'el equipo nunca reportó su cola',
                     'pendientes': 0},
        'cargos': <Object?>[], 'total_cargado': '0.00',
        'mensaje': mensaje,
      };

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    switch (ruta) {
      case '/v1/oficina/clientes/cl1/bloqueo':
        bloqueado = cuerpo['bloquear']! as bool;
        return RespuestaHttp(200, jsonEncode(_fichaCliente(mensaje: 'Listo.')));
      case '/v1/cortes':
        return RespuestaHttp(200, jsonEncode(_corte()));
      case '/v1/cortes/lq1/conteo':
        final contados = (cuerpo['contados']! as Map).cast<String, String>();
        contada = '${contados['r1']}.000';
        return RespuestaHttp(200, jsonEncode(_corte(mensaje: 'Conteo guardado.')));
      case '/v1/cortes/lq1/cerrar':
        if (cuerpo['confirmo_sincronizado'] != true) {
          return const RespuestaHttp(409, '{"detail":"Confirma que el teléfono terminó"}');
        }
        cerrado = true;
        return RespuestaHttp(200, jsonEncode(_corte(mensaje: 'Liquidación LQ-000001 cerrada.')));
    }
    return const RespuestaHttp(405, '{}');
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

Future<_ServidorDeOficina> _montar(
  WidgetTester tester, {
  List<String> permisos = const [
    'tablero.ver', 'ventas.ver_todas', 'inventario.cargar', 'inventario.liquidar',
  ],
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
    testWidgets('la oficina ve Tablero, Empresa, Vendedores, Clientes y Camiones',
        (tester) async {
      await _montar(tester);
      for (final k in ['nav_tablero', 'nav_empresa', 'boton_vendedores', 'nav_clientes',
          'nav_camiones']) {
        expect(find.byKey(Key(k)), findsOneWidget, reason: k);
      }
    });

    testWidgets('sin esos permisos, esas pestañas no salen', (tester) async {
      await _montar(tester, permisos: const ['tablero.ver']);
      expect(find.byKey(const Key('nav_tablero')), findsOneWidget);
      expect(find.byKey(const Key('boton_vendedores')), findsNothing);
      expect(find.byKey(const Key('nav_clientes')), findsNothing);
      expect(find.byKey(const Key('nav_camiones')), findsNothing);
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
    final listaDelPeriodo = find
        .descendant(of: find.byKey(const Key('lista_periodo')), matching: find.byType(Scrollable))
        .first;

    testWidgets('en el mismo Tablero: la semana trae lo vendido, por vendedor y por día',
        (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('periodo_semana')));
      await tester.pumpAndSettle();

      expect(servidor.pedidas.last.$1, '/v1/tablero/periodo');
      expect(servidor.pedidas.last.$2, {'periodo': 'semana'});
      expect(find.text('Del lunes 21 de septiembre al jueves 24 de septiembre'),
          findsOneWidget);
      expect(find.byKey(const Key('periodo_vendido')), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const Key('periodo_vendedor_VEND01')), 200, scrollable: listaDelPeriodo);
      await tester.scrollUntilVisible(
        find.byKey(const Key('periodo_dia_2026-09-23')), 200, scrollable: listaDelPeriodo);
      expect(find.text('miércoles 23 de septiembre'), findsOneWidget);
    });

    testWidgets('tocar un día abre el tablero completo de ese día, y Hoy regresa',
        (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('periodo_semana')));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.byKey(const Key('periodo_dia_2026-09-23')), 200, scrollable: listaDelPeriodo);
      await tester.tap(find.byKey(const Key('periodo_dia_2026-09-23')));
      await tester.pumpAndSettle();

      expect(servidor.pedidas.last.$1, '/v1/tablero');
      expect(servidor.pedidas.last.$2, {'fecha': '2026-09-23'});
      // El botón del día dice cuál es.
      expect(
        find.descendant(
          of: find.byKey(const Key('periodo_un_dia')),
          matching: find.text('miércoles 23 de septiembre'),
        ),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const Key('periodo_hoy')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$1, '/v1/tablero');
      expect(servidor.pedidas.last.$2, isNot(contains('fecha')));
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

  group('clientes', () {
    testWidgets('la lista pone arriba al que debe, y filtra los vencidos', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_clientes')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('cliente_oficina_CLI-1')), findsOneWidget);
      expect(textoQueContiene(r'Debe $400.00'), findsOneWidget);
      expect(textoQueContiene('nunca ha comprado'), findsOneWidget);

      await tester.ensureVisible(find.byKey(const Key('filtro_cliente_vencidos')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('filtro_cliente_vencidos')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$2, {'filtro': 'vencidos'});
      expect(find.byKey(const Key('cliente_oficina_CLI-2')), findsNothing);
    });

    testWidgets('su ficha: estado de cuenta, compras y bloquear con motivo', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_clientes')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cliente_oficina_CLI-1')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_ficha_cliente')), findsOneWidget);
      expect(find.text(r'Debe $400.00'), findsOneWidget);
      expect(textoQueContiene('Vencida hace 13 día(s)'), findsOneWidget);

      await tester.tap(find.byKey(const Key('boton_bloqueo_cliente')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('campo_motivo_bloqueo')), 'Debe tres notas');
      await tester.tap(find.byKey(const Key('boton_bloquear_de_verdad')));
      await tester.pumpAndSettle();

      expect(servidor.posts.last.$2, {'bloquear': true, 'motivo': 'Debe tres notas'});
      expect(textoQueContiene('Crédito BLOQUEADO: Debe tres notas'), findsOneWidget);
    });
  });

  group('el corte del día', () {
    testWidgets('abrir, contar y cerrar confirmando la sincronización', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_camiones')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('pestana_cortes')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('por_cortar_CG-000001')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_corte')), findsOneWidget);
      expect(servidor.posts.last.$2, {'carga_id': 'cg1'});
      // El conteo nace vacío: lo que no se anota vale cero.
      expect(find.text('Faltan 60'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('contado_Atún')), '60');
      await tester.tap(find.byKey(const Key('boton_guardar_conteo')));
      await tester.pumpAndSettle();
      expect(servidor.posts.last.$2, {'contados': {'r1': '60'}});
      expect(find.text('Faltan 60'), findsNothing);

      await tester.tap(find.byKey(const Key('boton_cerrar_corte')));
      await tester.pumpAndSettle();
      // Sin dato de sincronización, hay que marcar la casilla para poder cerrar.
      expect(
        tester.widget<FilledButton>(find.byKey(const Key('boton_cerrar_de_verdad'))).onPressed,
        isNull,
      );
      await tester.tap(find.byKey(const Key('casilla_confirmo_sincronizado')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('boton_cerrar_de_verdad')));
      await tester.pumpAndSettle();

      expect(servidor.posts.last.$2, {'confirmo_sincronizado': true});
      expect(find.text('Liquidación LQ-000001 cerrada.'), findsOneWidget);
      expect(find.byKey(const Key('boton_cerrar_corte')), findsNothing);
    });
  });
}
