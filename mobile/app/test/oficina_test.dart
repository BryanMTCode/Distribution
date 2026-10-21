/// El portal de la oficina: lo mismo que el dashboard, en el teléfono.
///
/// Pedidos en operación (octubre 2026): ver el almacén de cada vendedor, sus
/// detalles y sus ventas; los datos por día y por periodo; un resumen de la
/// empresa; y el día de los datos siempre a la vista.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_app/src/marca.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

const _periodos = [
  ['hoy', 'Hoy'], ['ayer', 'Ayer'], ['semana', 'Esta semana'],
  ['semana_pasada', 'Semana pasada'], ['mes', 'Este mes'],
  ['mes_pasado', 'Mes pasado'], ['todo', 'Todo'], ['rango', 'Personalizado'],
];

Map<String, Object?> _periodo(String clave, String desde, String hasta) => {
      'clave': clave, 'etiqueta': clave, 'desde': desde, 'hasta': hasta,
      'descripcion': clave,
    };

/// Un servidor de mentira con lo que piden las pantallas de la oficina.
class _ServidorDeOficina implements Transporte {
  final List<(String, Map<String, String>?)> pedidas = [];

  /// Sin el resumen de la empresa: sin señal a media consulta, o sin permiso.
  bool sinResumenDeEmpresa = false;

  /// El periodo sin la transferencia de un teléfono viejo: todo en efectivo,
  /// como es desde la +28 (ADR 0002 §81).
  bool todoEnEfectivo = false;

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    if (sinResumenDeEmpresa && ruta == '/v1/tablero/empresa') {
      return const RespuestaHttp(503, '{"detail":"sin resumen"}');
    }
    final clave = parametros?['periodo'] ?? 'hoy';
    final (desde, hasta) = switch (clave) {
      'semana' => ('2026-09-21', '2026-09-24'),
      'todo' => ('2026-08-03', '2026-09-24'),
      'rango' => (parametros!['desde']!, parametros['hasta']!),
      _ => ('2026-09-24', '2026-09-24'),
    };
    final cuerpo = switch (ruta) {
      '/v1/vendedores' when parametros?['desactivados'] == 'true' => {
          'periodo': _periodo(clave, desde, hasta),
          'periodos': _periodos,
          'desactivados': 1,
          'vendedores': [
            {
              'id': 'v9', 'codigo': 'VEND09', 'nombre': 'Pedro Baja', 'activo': false,
              'camion': null, 'rutas': null, 'ventas': 0, 'importe': '0.00',
              'efectivo': '0.00', 'no_ventas': 0, 'mermas': 0,
              'ultimo_contacto': null, 'saldo_cuenta': '0.00',
            },
          ],
        },
      '/v1/vendedores' => {
          'periodo': _periodo(clave, desde, hasta),
          'periodos': _periodos,
          'desactivados': 1,
          'vendedores': [
            {
              'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'activo': true,
              'camion': 'Camión 01', 'rutas': 'R04', 'ventas': 3, 'importe': '2250.00',
              'efectivo': '2250.00', 'no_ventas': 1, 'mermas': 0,
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
      // El mapa de rutas del miércoles 23 (ADR 0002 §168): cómo le fue a cada una.
      '/v1/vendedores/v1/ruta' when parametros?['fecha'] == '2026-09-23' => {
          'vendedor': 'Juan Pérez', 'rutas': ['R1101'], 'fecha': '2026-09-23', 'dia': 3,
          'es_hoy': true, 'sin_ubicacion': 1, 'dias': <Object?>[],
          'tiendas': [
            for (final (id, orden, extra) in [
              ('c300', 1, {'visita': 'venta', 'importe': '350.00'}),
              ('c301', 2, {'visita': 'no_venta'}),
              ('c302', 3, <String, Object?>{}),
              ('c303', 1, {'reprogramada': true}),
              ('c304', null, {'fuera_de_dia': true, 'visita': 'venta', 'importe': '90.00',
                              'ruta': 'R1107'}),
              // Cada quince días, la primera semana: el 23 no le toca.
              ('c306', 4, {'semanas': [1]}),
            ])
              {'id': id, 'codigo': id.substring(1), 'nombre': 'TIENDA $id',
               'direccion': null, 'ruta': 'R1101', 'orden': orden, 'con_ubicacion': true,
               'lat': '23.24${orden ?? 9}00', 'lng': '-106.43${orden ?? 9}00', ...extra},
            {'id': 'c305', 'codigo': '305', 'nombre': 'SIN UBICACIÓN', 'direccion': null,
             'ruta': 'R1101', 'orden': 5, 'con_ubicacion': false},
          ],
        },
      '/v1/vendedores/v1/ruta' => {
          'vendedor': 'Juan Pérez', 'rutas': ['R1101'], 'fecha': '2026-09-24',
          // Sin `dia`, el de hoy: jueves 24.
          'dia': int.parse(parametros?['dia'] ?? '4'),
          'es_hoy': (parametros?['dia'] ?? '4') == '4',
          'sin_ubicacion': 1,
          'dias': [
            for (final (d, e, n) in [
              (1, 'Lun', 45), (2, 'Mar', 44), (3, 'Mié', 47), (4, 'Jue', 3), (5, 'Vie', 37),
              (6, 'Sáb', 41),
            ])
              {'dia': d, 'etiqueta': e, 'tiendas': n},
          ],
          'tiendas': parametros?['dia'] == '1'
              ? [
                  {'id': 'c100', 'codigo': '100', 'nombre': 'PUESTO PLAYA SUR',
                   'direccion': 'EMBARCADERO DE LA PUNTILLA S/N, PLAYA SUR', 'ruta': 'R1101',
                   'orden': 1, 'con_ubicacion': false},
                ]
              : [
                  {'id': 'c250', 'codigo': '250', 'nombre': 'SUPER ATENAS',
                   'direccion': 'CARNAVAL 819, PLAYA SUR', 'ruta': 'R1101', 'orden': 1,
                   'con_ubicacion': true, 'visita': 'venta', 'importe': '350.00'},
                  {'id': 'c251', 'codigo': '251', 'nombre': 'OLAS NEGRAS',
                   'direccion': 'CARNAVAL 911, PLAYA SUR', 'ruta': 'R1101', 'orden': 2,
                   'con_ubicacion': true, 'visita': 'no_venta'},
                  {'id': 'c252', 'codigo': '252', 'nombre': 'EL FARO', 'direccion': null,
                   'ruta': 'R1101', 'orden': 3, 'con_ubicacion': false},
                ],
        },
      '/v1/vendedores/v1/camion' => {
          'vendedor': 'Juan Pérez', 'camion': 'Camión 01', 'piezas': '55.000',
          'ultimo_contacto': null,
          'existencias': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'cantidad': '60.000', 'familia': 'Enlatados'},
            {'producto_id': 'p3', 'sku': 'SARD', 'nombre': 'Sardina 425 g',
             'unidad_base': 'PZA', 'cantidad': '24.000', 'familia': 'Enlatados'},
            {'producto_id': 'p2', 'sku': 'MARU', 'nombre': 'Maruchan',
             'unidad_base': 'PZA', 'cantidad': '-5.000', 'familia': 'Sopas'},
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
            'efectivo': todoEnEfectivo ? '2250.00' : '2000.00',
            'transferencias': todoEnEfectivo ? '0.00' : '250.00', 'total': '2250.00',
            'canceladas': 0, 'mermas': 0, 'devoluciones': 0,
            'no_ventas': 1, 'clientes_atendidos': 2, 'clientes_nuevos': 0,
          },
          'por_vendedor': [
            {'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'ventas': 3,
             'importe': '2250.00', 'efectivo': todoEnEfectivo ? '2250.00' : '2000.00',
             'transferencias': todoEnEfectivo ? '0.00' : '250.00',
             'mermas': 0, 'no_ventas': 1},
          ],
          'por_dia': [
            {'fecha': '2026-09-24', 'ventas': 3, 'importe': '2250.00', 'efectivo': '2000.00'},
            {'fecha': '2026-09-23', 'ventas': 0, 'importe': '0.00', 'efectivo': '0.00'},
          ],
        },
      '/v1/almacen/articulos' => [
          for (final (sku, nombre, familia, precio) in [
            ('S-100', 'Sobre Minino salmón', 'Ganador Minino', '10.00'),
            ('B-100', 'Cacahuate japonés', 'Botanas Javi', null),
          ])
            {
              'producto_id': sku, 'sku': sku, 'nombre': nombre, 'familia': familia,
              'unidad_base': 'PZA', 'precio': precio, 'en_bodegas': '1056.000',
              'en_camiones': '24.000',
            },
        ],
      '/v1/tablero/empresa' => {
          'clientes_activos': 120, 'prospectos': 4, 'clientes_inactivos': 2,
          'clientes_nuevos_mes': 6, 'vendedores': 5,
          'vendedores_con_camion': 4, 'usuarios_oficina': 3, 'rutas': 5,
          'telefonos': 5, 'productos': 210, 'productos_sin_precio': 1, 'bodegas': 1,
          'camiones': 5, 'piezas_en_bodegas': '4800.000',
          'piezas_en_camiones': '960.000', 'existencias_negativas': 0,
          'vendido_mes': '98000.00', 'vendido_anio': '980000.00',
          'mayoreo_mes': '5000.00', 'mayoreo_anio': '5000.00',
        },
      '/v1/oficina/clientes' => _listaDeClientes(parametros),
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
  String contada = '0.000';
  bool cerrado = false;

  Map<String, Object?> _fichaCliente({String? mensaje}) => {
        'id': 'cl1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'razon_social': null,
        'contacto': 'Doña Mary', 'telefono': '6691234567', 'direccion': 'Juárez 10',
        'referencias': 'Portón verde', 'ruta': 'Ruta 4', 'estatus': 'activo',
        'lat': '23.2494100', 'lng': '-106.4111400', 'ubicacion_origen': 'gps',
        'ubicacion_capturada_en': '2026-09-20T17:00:00Z',
        'comprado_mes': '2750.00', 'comprado_anio': '2750.00',
        'ventas': [
          {'id': 'venta-1', 'folio': 'VEND01-000001', 'fecha': '2026-09-24',
           'momento': null, 'tipo': 'contado', 'forma_pago': 'transferencia',
           'estado': 'confirmada', 'total': '2250.00', 'vendedor': 'Juan Pérez'},
          {'id': 'venta-0', 'folio': 'VEND01-000000', 'fecha': '2026-09-02',
           'momento': null, 'tipo': 'credito', 'forma_pago': null,
           'estado': 'confirmada', 'total': '500.00', 'vendedor': 'Juan Pérez'},
        ],
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
      case '/v1/cortes/lq1/descartar':
        return const RespuestaHttp(
            200, '{"mensaje":"Corte descartado: su carga vuelve a «Sin cortar»."}');
    }
    return const RespuestaHttp(405, '{}');
  }
}

/// Como el servidor +39 (ADR 0002 §122): sin ruta, día ni búsqueda, solo las
/// rutas con sus tiendas de cada día; con ellos, esas tiendas en su orden.
Map<String, Object?> _listaDeClientes(Map<String, String>? p) {
  final filtro = p?['filtro'] ?? 'todos';
  final ruta = p?['ruta'] ?? '';
  final dia = p?['dia'] ?? '';
  final listado = ruta.isNotEmpty || dia.isNotEmpty || (p?['q'] ?? '').isNotEmpty;
  final todos = [
    {'id': 'cl1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'ruta': 'Ruta 4',
     'estatus': 'activo', 'telefono': null, 'con_ubicacion': true,
     'ultima_compra': '2026-09-24', 'ruta_id': 'r4', 'ruta_codigo': 'R04', 'dias': [1],
     'orden': 1},
    {'id': 'cl2', 'codigo': 'CLI-2', 'nombre': 'Sin Pin', 'ruta': 'Ruta 4',
     'estatus': 'activo', 'telefono': null, 'con_ubicacion': false, 'ultima_compra': null,
     'ruta_id': 'r4', 'ruta_codigo': 'R04', 'dias': [1, 4], 'orden': 2},
    {'id': 'cl3', 'codigo': 'CLI-3', 'nombre': 'La Pasadita', 'ruta': 'Ruta 9',
     'estatus': 'activo', 'telefono': null, 'con_ubicacion': true, 'ultima_compra': null,
     'ruta_id': 'r9', 'ruta_codigo': 'R09', 'dias': [2], 'orden': 1},
  ];
  return {
    'filtro': filtro, 'recortado': false, 'ruta': ruta, 'dia': dia, 'listado': listado,
    'conteos': {'todos': 3, 'prospectos': 0, 'sin_ubicacion': 1},
    'rutas': [
      {'clave': 'r4', 'codigo': 'R04', 'nombre': 'Ruta 4', 'vendedor': 'Juan Pérez',
       'total': 2,
       'por_dia': [{'dia': 1, 'tiendas': 2}, {'dia': 4, 'tiendas': 1}]},
      {'clave': 'r9', 'codigo': 'R09', 'nombre': 'Ruta 9', 'vendedor': null, 'total': 1,
       'por_dia': [{'dia': 2, 'tiendas': 1}]},
    ],
    'clientes': [
      if (listado)
        for (final c in todos)
          if ((ruta.isEmpty || c['ruta_id'] == ruta) &&
              (dia.isEmpty || (c['dias']! as List).contains(int.parse(dia))) &&
              (filtro != 'sin_ubicacion' || c['con_ubicacion'] == false))
            c,
    ],
  };
}

/// Los clientes se abren desde Empresa (ADR 0002 §152).
Future<void> _abrirClientes(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('nav_empresa')));
  await tester.pumpAndSettle();
  await tocar(tester, const Key('empresa_clientes'));
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
    testWidgets('la oficina ve Tablero, Empresa, Vendedores y Almacén; Clientes ya no va '
        'abajo (ADR 0002 §152)', (tester) async {
      await _montar(tester);
      for (final k in ['nav_tablero', 'nav_empresa', 'boton_vendedores', 'nav_almacen']) {
        expect(find.byKey(Key(k)), findsOneWidget, reason: k);
      }
      // «No veo necesario lo de Clientes abajo si se puede entrar desde Empresa.»
      expect(find.byKey(const Key('nav_clientes')), findsNothing);
      // Sin `mayoreo.vender`, sin Mayoreo.
      expect(find.byKey(const Key('nav_mayoreo')), findsNothing);
    });

    testWidgets('sin esos permisos, esas pestañas no salen', (tester) async {
      await _montar(tester, permisos: const ['tablero.ver']);
      expect(find.byKey(const Key('nav_tablero')), findsOneWidget);
      expect(find.byKey(const Key('boton_vendedores')), findsNothing);
      expect(find.byKey(const Key('nav_almacen')), findsNothing);
    });

    testWidgets('si el resumen de la empresa no llega, los clientes se abren igual',
        (tester) async {
      final servidor = await _montar(tester);
      servidor.sinResumenDeEmpresa = true;
      await tester.tap(find.byKey(const Key('nav_empresa')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('empresa_clientes')), findsNothing);
      // Sin `inventario.ver`, el inventario no sale.
      expect(find.byKey(const Key('empresa_ir_inventario')), findsNothing);
      await tester.tap(find.byKey(const Key('empresa_ir_clientes')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$1, '/v1/oficina/clientes');
      expect(find.byKey(const Key('resumen_ruta_R04')), findsOneWidget);
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
      // Toda venta es en efectivo (§81): lo vendido no se repite como
      // «en efectivo» (§154).
      expect(textoQueContiene(r'3 venta(s) · $2,250.00 · 1 sin venta'), findsOneWidget);
      expect(textoQueContiene('en efectivo'), findsNothing);
      expect(textoQueContiene(r'Debe $35.00'), findsOneWidget);
    });

    testWidgets('los desactivados no se mezclan: van en su propia lista (ADR 0002 §93)',
        (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      expect(find.text('Pedro Baja'), findsNothing);

      await tocar(tester, const Key('ver_vendedores_desactivados'));
      expect(find.byKey(const Key('pantalla_vendedores_desactivados')), findsOneWidget);
      expect(servidor.pedidas.last.$2, {'periodo': 'hoy', 'desactivados': 'true'});
      expect(find.text('Pedro Baja'), findsOneWidget);
      expect(find.text('Juan Pérez'), findsNothing);
      expect(find.byKey(const Key('ver_vendedores_desactivados')), findsNothing);
    });

    testWidgets('«Todo» pide todo y dice desde cuándo (ADR 0002 §96)', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('periodo_todo')).first);
      await tester.tap(find.byKey(const Key('periodo_todo')).first);
      await tester.pumpAndSettle();

      expect(servidor.pedidas.last.$2, {'periodo': 'todo'});
      expect(textoQueContiene('Todo, del lunes 3 de agosto al jueves 24 de septiembre'),
          findsOneWidget);
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

      // Su camión, por familia y con el negativo marcado (ADR 0002 §114).
      await tester.tap(find.byKey(const Key('pestana_camion')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('camion_ATUN-140')), findsOneWidget);
      expect(find.text('60 PZA'), findsOneWidget);
      expect(textoQueContiene('En negativo'), findsOneWidget);
      expect(find.text('Enlatados · 2'), findsOneWidget);
      expect(find.text('84 pzas'), findsOneWidget);
      expect(find.text('Sopas · 1'), findsOneWidget);
      // Una familia se pliega con un toque, y las demás se quedan como están.
      await tester.tap(find.byKey(const Key('familia_Enlatados')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('camion_ATUN-140')), findsNothing);
      expect(find.byKey(const Key('camion_SARD')), findsNothing);
      expect(find.byKey(const Key('camion_MARU')), findsOneWidget);
    });

    testWidgets('el mapa de rutas: un vendedor y un día, con cómo le fue a cada tienda '
        '(ADR 0002 §168)', (tester) async {
      final servidor = await _montar(tester);
      tester.view.physicalSize = const Size(360, 760);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('abrir_mapa_de_rutas')));
      await tester.pumpAndSettle();

      // Abre con el primer vendedor y hoy.
      expect(find.byKey(const Key('pantalla_mapa_de_rutas')), findsOneWidget);
      expect(find.byKey(const Key('mapa_rutas_vendedor')), findsOneWidget);
      expect(servidor.pedidas.last.$1, '/v1/vendedores/v1/ruta');
      expect(servidor.pedidas.last.$2, {'fecha': '2026-09-24'});

      // Ayer.
      await tester.tap(find.byKey(const Key('boton_dia_anterior_mapa_rutas')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(servidor.pedidas.last.$1, '/v1/vendedores/v1/ruta');
      expect(servidor.pedidas.last.$2, {'fecha': '2026-09-23'});
      expect(
        find.text('5 tienda(s) · 1 vendida(s) · 1 sin venta · 3 sin visitar · '
            '1 reagendada(s) · 1 fuera de su día · 1 sin ubicación (no salen)'),
        findsOneWidget,
      );
      for (final id in ['c300', 'c301', 'c302', 'c303', 'c304']) {
        expect(find.byKey(Key('pin_ruta_$id')), findsOneWidget, reason: id);
      }
      expect(find.byKey(const Key('pin_ruta_c305')), findsNothing, reason: 'sin ubicación');
      expect(find.byKey(const Key('pin_ruta_c306')), findsNothing, reason: 'no es su semana');
      Color? color(String id) => (tester
              .widget<DecoratedBox>(find.byKey(Key('circulo_ruta_$id')))
              .decoration as BoxDecoration)
          .color;
      expect(color('c300'), colorDeVendida);
      expect(color('c302'), rojoDistribucionesSE);
      expect(color('c303'), colorDeReagendada);
      // El recorrido, en el orden de su hoja.
      expect(find.byKey(const Key('linea_de_la_ruta')), findsOneWidget);
      expect(find.text('No se visitó'), findsOneWidget, reason: 'en la leyenda: el día ya pasó');

      await tester.tap(find.byKey(const Key('pin_ruta_c304')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('ficha_ruta_c304')), findsOneWidget);
      expect(find.text('Fuera de su día · vendida \$90.00'), findsOneWidget);
    });

    testWidgets('desde un vendedor, el mapa abre con él', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('vendedor_VEND01')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('abrir_mapa_de_su_ruta')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_mapa_de_rutas')), findsOneWidget);
      expect(servidor.pedidas.last.$1, '/v1/vendedores/v1/ruta');
      expect(servidor.pedidas.last.$2, {'fecha': '2026-09-24'});
    });

    testWidgets('su ruta de cada día: las tiendas en orden y lo de hoy (ADR 0002 §121)',
        (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('boton_vendedores')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('vendedor_VEND01')));
      await tester.pumpAndSettle();
      // En el ancho del teléfono de la empresa (Hyundai HYLine Pro, 360).
      tester.view.physicalSize = const Size(360, 760);
      await tester.tap(find.byKey(const Key('pestana_ruta')));
      await tester.pumpAndSettle();

      // Se pide la de hoy, con el día operativo de la app.
      final pedida = servidor.pedidas.lastWhere((p) => p.$1 == '/v1/vendedores/v1/ruta');
      expect(pedida.$2, {'fecha': '2026-09-24'});
      expect(find.text('Ruta R1101'), findsOneWidget);
      expect(find.text('Lun 45'), findsOneWidget);
      expect(find.text('Sáb 41'), findsOneWidget);
      expect(
        find.text('Hoy, jueves: 3 tiendas · visitadas 2 (1 con venta), faltan 1'),
        findsOneWidget,
      );
      expect(find.byKey(const Key('tienda_ruta_250')), findsOneWidget);
      expect(find.text('\$350.00'), findsOneWidget);
      expect(find.text('sin venta'), findsOneWidget);
      expect(find.text('pendiente'), findsOneWidget);
      // La que ya compró, en verde; la visitada sin venta, en gris; la que
      // falta, en el rojo de la marca con su número (ADR 0002 §153).
      Color? fondo(String codigo) => tester
          .widget<CircleAvatar>(find.byKey(Key('orden_ruta_$codigo')))
          .backgroundColor;
      expect(fondo('250'), colorDeVendida);
      expect(fondo('251'), colorDeSinVenta);
      expect(fondo('252'), isNull);
      expect(textoQueContiene('252 · sin ubicación'), findsOneWidget);
      expect(find.byKey(const Key('sin_ubicacion_ruta')), findsOneWidget);

      // Otro día: las suyas, sin «pendiente».
      await tester.tap(find.byKey(const Key('dia_ruta_1')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$2, {'dia': '1', 'fecha': '2026-09-24'});
      expect(find.text('El lunes: 1 tienda'), findsOneWidget);
      expect(find.byKey(const Key('tienda_ruta_100')), findsOneWidget);
      expect(find.byKey(const Key('tienda_ruta_250')), findsNothing);
      expect(find.text('pendiente'), findsNothing);
      // Otro día no hay visitas que pintar.
      expect(
        tester.widget<CircleAvatar>(find.byKey(const Key('orden_ruta_100'))).backgroundColor,
        isNull,
      );
      expect(
        textoQueContiene('100 · EMBARCADERO DE LA PUNTILLA S/N, PLAYA SUR · sin ubicación'),
        findsOneWidget,
      );
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

    testWidgets('todo en efectivo: el periodo no repite lo vendido como efectivo (§154)',
        (tester) async {
      final servidor = await _montar(tester);
      servidor.todoEnEfectivo = true;
      await tester.tap(find.byKey(const Key('periodo_semana')));
      await tester.pumpAndSettle();
      expect(
        find.descendant(
          of: find.byKey(const Key('periodo_vendido')),
          matching: find.textContaining('Todo en efectivo'),
        ),
        findsOneWidget,
      );
      expect(textoQueContiene('Transferencia'), findsNothing);
      await tester.scrollUntilVisible(
        find.byKey(const Key('periodo_vendedor_VEND01')), 200, scrollable: listaDelPeriodo);
      expect(
        find.descendant(
          of: find.byKey(const Key('periodo_vendedor_VEND01')),
          matching: find.textContaining('en efectivo'),
        ),
        findsNothing,
      );
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

    testWidgets('cada cifra de Empresa abre su lista (ADR 0002 §97)', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_empresa')));
      await tester.pumpAndSettle();

      await tocar(tester, const Key('empresa_articulos'));
      expect(find.byKey(const Key('pantalla_articulos')), findsOneWidget);
      expect(find.byKey(const Key('familia_Ganador Minino')), findsOneWidget);
      expect(find.byKey(const Key('articulo_S-100')), findsOneWidget);
      expect(textoQueContiene('sin precio'), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();

      await tocar(tester, const Key('empresa_vendido_mes'));
      expect(find.byKey(const Key('pantalla_vendedores')), findsOneWidget);
      expect(servidor.pedidas.last.$1, '/v1/vendedores');
      expect(servidor.pedidas.last.$2, {'periodo': 'mes'});
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
      // Lo vendido incluye el mayoreo, y lo dice (ADR 0002 §157).
      await tester.scrollUntilVisible(find.byKey(const Key('empresa_vendido_mes')), 200);
      expect(find.descendant(of: find.byKey(const Key('empresa_vendido_mes')),
          matching: find.textContaining(r'incluye $5,000 de mayoreo')), findsOneWidget);
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
    testWidgets('abre con las rutas y sus días; una ruta trae sus tiendas por día (§122)',
        (tester) async {
      final servidor = await _montar(tester);
      // En el ancho del teléfono de la empresa (Hyundai HYLine Pro, 360).
      tester.view.physicalSize = const Size(360, 760);
      await _abrirClientes(tester);

      // No todas en general: las rutas, con sus tiendas de cada día.
      expect(servidor.pedidas.last.$2, {'filtro': 'todos', 'agrupar': '1'});
      expect(find.byKey(const Key('resumen_ruta_R04')), findsOneWidget);
      expect(textoQueContiene('Juan Pérez · 2 tiendas'), findsOneWidget);
      expect(textoQueContiene('Lun 2 · Jue 1'), findsOneWidget);
      expect(find.byKey(const Key('cliente_oficina_CLI-1')), findsNothing);

      await tester.tap(find.byKey(const Key('resumen_ruta_R04')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$2, {'filtro': 'todos', 'ruta': 'r4', 'agrupar': '1'});
      expect(find.byKey(const Key('grupo_ruta_R04')), findsOneWidget);
      expect(find.text('Lunes · 2 tiendas'), findsOneWidget);
      expect(find.text('Jueves · 1 tienda'), findsOneWidget);
      // La que toca dos días sale en los dos.
      expect(find.byKey(const Key('cliente_oficina_CLI-2')), findsNWidgets(2));
      expect(find.byKey(const Key('cliente_oficina_CLI-3')), findsNothing);

      // Los días con su cuenta, de la ruta elegida.
      expect(find.text('Lun 2'), findsOneWidget);
      await tester.ensureVisible(find.byKey(const Key('dia_cliente_4')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('dia_cliente_4')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$2,
          {'filtro': 'todos', 'ruta': 'r4', 'dia': '4', 'agrupar': '1'});
      expect(find.text('Lunes · 2 tiendas'), findsNothing);
      expect(find.byKey(const Key('cliente_oficina_CLI-2')), findsOneWidget);

      // Todas las rutas, el martes.
      await tester.ensureVisible(find.byKey(const Key('ruta_cliente_todas')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('ruta_cliente_todas')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('dia_cliente_2')));
      await tester.tap(find.byKey(const Key('dia_cliente_2')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('grupo_ruta_R09')), findsOneWidget);
      expect(find.byKey(const Key('cliente_oficina_CLI-3')), findsOneWidget);
    });

    testWidgets('la lista no habla de deudas, y filtra los que no tienen ubicación',
        (tester) async {
      final servidor = await _montar(tester);
      await _abrirClientes(tester);
      await tester.tap(find.byKey(const Key('resumen_ruta_R04')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('cliente_oficina_CLI-1')), findsOneWidget);
      expect(textoQueContiene('nunca ha comprado'), findsWidgets);
      expect(textoQueContiene('Debe'), findsNothing);
      expect(find.byKey(const Key('filtro_cliente_vencidos')), findsNothing);

      await tester.ensureVisible(find.byKey(const Key('filtro_cliente_sin_ubicacion')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('filtro_cliente_sin_ubicacion')));
      await tester.pumpAndSettle();
      expect(servidor.pedidas.last.$2, {'filtro': 'sin_ubicacion', 'ruta': 'r4', 'agrupar': '1'});
      expect(find.byKey(const Key('cliente_oficina_CLI-1')), findsNothing);
      expect(find.byKey(const Key('cliente_oficina_CLI-2')), findsWidgets);
    });

    testWidgets('su ficha: ubicación y compras con su forma de pago', (tester) async {
      await _montar(tester);
      await _abrirClientes(tester);
      await tester.tap(find.byKey(const Key('resumen_ruta_R04')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cliente_oficina_CLI-1')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_ficha_cliente')), findsOneWidget);
      expect(textoQueContiene(r'Compró $2,750.00 este mes'), findsOneWidget);
      expect(textoQueContiene('23.249410, -106.411140 (GPS)'), findsOneWidget);
      expect(textoQueContiene('transferencia'), findsOneWidget);
      // La venta a crédito del piloto sigue en su historia, dicha como tal.
      expect(textoQueContiene('crédito (piloto)'), findsOneWidget);
      expect(textoQueContiene('Debe'), findsNothing);
      expect(find.byKey(const Key('boton_bloqueo_cliente')), findsNothing);
    });
  });

  group('el corte del día', () {
    testWidgets('el que se abrió sin querer se descarta (§157)', (tester) async {
      // «Me aparecen dos abiertos cuando solo di click a los nombres.»
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_almacen')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('pestana_cortes')));
      await tester.tap(find.byKey(const Key('pestana_cortes')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('por_cortar_CG-000001')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('confirmar_abrir_corte')));
      await tester.pumpAndSettle();

      await tocar(tester, const Key('boton_descartar_corte'));
      // Doble confirmación, como todo lo que se quita (§143).
      await tester.tap(find.byKey(const Key('confirmar_quitar_si')));
      await tester.pumpAndSettle();
      expect(servidor.posts.last.$1, '/v1/cortes/lq1/descartar');
      expect(find.byKey(const Key('pantalla_corte')), findsNothing);
      expect(textoQueContiene('Corte descartado'), findsOneWidget);
    });

    testWidgets('abrir, contar y cerrar confirmando la sincronización', (tester) async {
      final servidor = await _montar(tester);
      await tester.tap(find.byKey(const Key('nav_almacen')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('pestana_cortes')));
      await tester.tap(find.byKey(const Key('pestana_cortes')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('por_cortar_CG-000001')));
      await tester.pumpAndSettle();
      // Pregunta antes de abrir el corte (ADR 0002 §157).
      await tester.tap(find.byKey(const Key('confirmar_abrir_corte')));
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
