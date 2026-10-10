/// La pestaña Almacén del portal de la oficina: existencias, entradas y
/// traspasos entre bodegas.
///
/// Pedido en operación (octubre 2026): «quiero agregar mercancía desde la app y
/// traspasar entre almacenes: manejar todo el negocio en modo gerencia».
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

const _presentaciones = [
  {'unidad': 'CAJA', 'factor': '24.000'},
  {'unidad': 'PZA', 'factor': '1.000'},
];

const _bodega = {
  'id': 'b1', 'codigo': 'BODEGA_PRINCIPAL', 'nombre': 'Bodega', 'tipo': 'bodega',
  'responsable': null, 'productos': 1, 'piezas': '760.000', 'negativos': 0,
  'valor': '7600.00',
};

const _camion = {
  'id': 'c1', 'codigo': 'CAMION_01', 'nombre': 'Camión 01', 'tipo': 'camion',
  'responsable': 'Juan Pérez', 'productos': 1, 'piezas': '0.000', 'negativos': 1,
};

/// Un servidor de almacén de mentira, con lo justo para recorrer los flujos.
class _ServidorDeAlmacen implements Transporte {
  _ServidorDeAlmacen({this.bodegas = 2, this.noAlcanza = false});

  final int bodegas;
  final bool noAlcanza;
  final List<(String, Map<String, String>?)> pedidas = [];
  final List<(String, Map<String, Object?>)> posts = [];

  String _motivo = 'compra';
  String? _nota;
  String _estado = 'borrador';
  final List<Map<String, Object?>> _renglones = [];
  bool _traspasado = false;

  List<Map<String, Object?>> get _opcionesDeBodega => [
        {'id': 'b1', 'codigo': 'BODEGA_PRINCIPAL', 'nombre': 'Bodega'},
        if (bodegas > 1) {'id': 'b2', 'codigo': 'BODEGA_NORTE', 'nombre': 'Bodega Norte'},
      ];

  Map<String, Object?> _entrada({String? mensaje}) {
    final compra = _motivo == 'compra';
    final confirmada = _estado == 'confirmada';
    return {
      'id': 'e1', 'folio': 'EN-000001', 'motivo': _motivo,
      'motivo_etiqueta': compra ? 'Compra a proveedor' : 'Inventario inicial',
      'estado': _estado, 'editable': _estado == 'borrador', 'exige_costo': compra,
      'proveedor': compra ? 'Abarrotes del Centro' : null,
      'referencia': compra ? 'F-100' : null, 'nota': _nota,
      'fecha_operativa': '2026-09-24', 'bodega': 'Bodega', 'almacen_destino_id': 'b1',
      'renglones': _renglones,
      'total_piezas': _renglones.isEmpty ? '0.000' : '240.000',
      'importe_capturado': _renglones.isEmpty ? '0.00' : '2960.00',
      'sin_costo': 0,
      'cuenta': confirmada && compra
          ? {'proveedor': 'Abarrotes del Centro', 'importe_original': '2960.00',
             'saldo': '2960.00', 'estado': 'pendiente',
             'fecha_vencimiento': '2026-10-24'}
          : null,
      'mensaje': mensaje,
    };
  }

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    final Object? cuerpo = switch (ruta) {
      '/v1/almacen' => [_bodega, _camion],
      '/v1/almacen/b1/existencias' => {
          'almacen': _bodega,
          'existencias': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'cantidad': '760.000', 'presentaciones': _presentaciones,
             'precio': '10.00', 'valor': '7600.00', 'familia': 'Abarrotes'},
            {'producto_id': 'p2', 'sku': 'COCA600', 'nombre': 'Coca 600 ml',
             'unidad_base': 'PZA', 'cantidad': '12.000', 'presentaciones': _presentaciones,
             'precio': null, 'valor': null},
          ],
        },
      '/v1/almacen/c1/existencias' => {
          'almacen': _camion,
          'existencias': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'cantidad': '-3.000', 'presentaciones': _presentaciones},
          ],
        },
      '/v1/almacen/productos' => [
          {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
           'unidad_base': 'PZA', 'maneja_lote': false, 'existencia': '760.000',
           'presentaciones': _presentaciones},
          if (parametros?['solo_con_existencia'] != 'true')
            {'producto_id': 'p2', 'sku': 'COCA600', 'nombre': 'Coca 600 ml',
             'unidad_base': 'PZA', 'maneja_lote': false, 'existencia': '0.000',
             'presentaciones': _presentaciones},
        ],
      '/v1/almacen/entradas' => {
          'entradas': <Object?>[],
          'bodegas': _opcionesDeBodega,
          'proveedores': [{'id': 'pr1', 'codigo': 'ABARR01', 'nombre': 'Abarrotes del Centro'}],
          'motivos': [
            {'clave': 'compra', 'etiqueta': 'Compra a proveedor',
             'explicacion': 'Llegó mercancía del proveedor.'},
            {'clave': 'inicial', 'etiqueta': 'Inventario inicial',
             'explicacion': 'Lo que ya estaba en la bodega.'},
            {'clave': 'ajuste', 'etiqueta': 'Ajuste por conteo físico',
             'explicacion': 'El conteo encontró más.'},
          ],
        },
      '/v1/almacen/entradas/e1' => _entrada(),
      '/v1/almacen/traspasos' => {
          'traspasos': [
            if (_traspasado)
              {'id': 't1', 'folio': 'TR-000001', 'creado_en': '2026-09-24T19:05:00Z',
               'origen': 'Bodega', 'destino': 'Bodega Norte', 'quien': 'Bryan',
               'renglones': 1, 'piezas': '48.000', 'observaciones': 'Para el norte'},
          ],
          'bodegas': _opcionesDeBodega,
        },
      _ => null,
    };
    if (cuerpo == null) return const RespuestaHttp(404, '{"detail":"Not Found"}');
    return RespuestaHttp(200, jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    switch (ruta) {
      case '/v1/almacen/entradas':
        _motivo = cuerpo['motivo']! as String;
        _nota = (cuerpo['nota']! as String).isEmpty ? null : cuerpo['nota']! as String;
        return RespuestaHttp(
          200,
          jsonEncode(_entrada(mensaje: 'Entrada abierta: agrega lo que llegó.')),
        );
      case '/v1/almacen/entradas/e1/renglones':
        _renglones.add({
          'id': 'r1', 'sku': cuerpo['sku'], 'nombre': 'Coca 600 ml', 'unidad_base': 'PZA',
          'cantidad': '240.000', 'unidad_codigo': cuerpo['unidad'],
          'unidades_capturadas': '10.000', 'costo_unitario': '12.3333',
          'importe': '2960.00', 'lote': null, 'existencia': '0.000', 'proyectado': '240.000',
        });
        return RespuestaHttp(200, jsonEncode(_entrada(mensaje: '10 CAJA = 240 PZA.')));
      case '/v1/almacen/entradas/e1/confirmar':
        _estado = 'confirmada';
        return RespuestaHttp(200, jsonEncode(_entrada(mensaje: 'Entrada EN-000001 confirmada.')));
      case '/v1/almacen/traspasos':
        if (noAlcanza) {
          return const RespuestaHttp(
            409,
            '{"detail":"No alcanza en Bodega: Atún en agua 140 g (hay 760, pides 960)."}',
          );
        }
        _traspasado = true;
        return const RespuestaHttp(
          200,
          '{"id":"t1","folio":"TR-000001",'
          '"mensaje":"TR-000001: 1 producto(s), 48 piezas de Bodega a Bodega Norte."}',
        );
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

const _todo = [
  'tablero.ver', 'inventario.ver', 'inventario.ajustar', 'inventario.cargar',
  'inventario.liquidar',
];

Future<_ServidorDeAlmacen> _montar(
  WidgetTester tester, {
  List<String> permisos = _todo,
  _ServidorDeAlmacen? servidor,
}) async {
  final s = servidor ?? _ServidorDeAlmacen();
  await montarApp(
    tester,
    ahora: DateTime.utc(2026, 9, 24, 19), // jueves 24, 12:00 en Mazatlán
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(s),
      sesionProvider.overrideWith(() => _Gerencia(permisos)),
    ],
  );
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('nav_almacen')));
  await tester.pumpAndSettle();
  return s;
}

Future<void> _irA(WidgetTester tester, String pestana) async {
  await tester.ensureVisible(find.byKey(Key(pestana)));
  await tester.tap(find.byKey(Key(pestana)));
  await tester.pumpAndSettle();
}

void main() {
  group('la pestaña', () {
    testWidgets('con todos los permisos: existencias, entradas, traspasos, cargas y '
        'corte', (tester) async {
      await _montar(tester);
      expect(find.byKey(const Key('pantalla_almacen')), findsOneWidget);
      for (final k in ['pestana_existencias', 'pestana_entradas', 'pestana_traspasos',
          'pestana_cargas', 'pestana_cortes']) {
        expect(find.byKey(Key(k)), findsOneWidget, reason: k);
      }
      // El corte y la carga del vendedor van cada uno en su pestaña (ADR 0002 §82).
      expect(find.byKey(const Key('pestana_cierres')), findsNothing);
    });

    testWidgets('con solo ver, las listas se ven pero sin botón de nueva', (tester) async {
      await _montar(tester, permisos: const ['tablero.ver', 'inventario.ver']);
      expect(find.byKey(const Key('pestana_cargas')), findsNothing);
      await _irA(tester, 'pestana_entradas');
      expect(find.byKey(const Key('pantalla_entradas')), findsOneWidget);
      expect(find.byKey(const Key('boton_nueva_entrada')), findsNothing);
      await _irA(tester, 'pestana_traspasos');
      expect(find.byKey(const Key('boton_nuevo_traspaso')), findsNothing);
    });
  });

  group('existencias', () {
    testWidgets('cada almacén con lo que tiene, en piezas y en cajas', (tester) async {
      await _montar(tester);
      await _irA(tester, 'pestana_existencias');
      expect(find.byKey(const Key('almacen_BODEGA_PRINCIPAL')), findsOneWidget);
      expect(textoQueContiene('1 en negativo'), findsNothing); // va en un TextSpan
      expect(find.textContaining('1 en negativo', findRichText: true), findsOneWidget);

      await tester.tap(find.byKey(const Key('almacen_BODEGA_PRINCIPAL')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_existencias_almacen')), findsOneWidget);
      // 760 piezas con caja de 24: 31 cajas y 16 piezas, como en el anaquel.
      expect(textoQueContiene('31 CAJA + 16 PZA'), findsOneWidget);
      expect(find.text('760 PZA'), findsOneWidget);
      // Y lo que vale, artículo por artículo y el almacén entero.
      expect(textoQueContiene(r'$10.00 c/u'), findsOneWidget);
      expect(tester.widget<Text>(find.byKey(const Key('valor_ATUN-140'))).data,
          r'$7,600.00');
      expect(tester.widget<Text>(find.byKey(const Key('valor_COCA600'))).data, '—');
      expect(textoQueContiene('Vale \$7,600.00 a precio de venta'), findsOneWidget);
      expect(textoQueContiene('1 artículo(s) sin precio no suman'), findsOneWidget);
      // Agrupados por familia, como en la hoja de la dirección; lo que no tiene
      // familia va al final, en «Sin familia».
      expect(find.byKey(const Key('familia_Abarrotes')), findsOneWidget);
      expect(find.byKey(const Key('familia_Sin familia')), findsOneWidget);
      expect(textoQueContiene('Abarrotes · 1'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('campo_buscar_existencia')), 'coca');
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('existencia_ATUN-140')), findsNothing);
      expect(find.byKey(const Key('existencia_COCA600')), findsOneWidget);
    });

    testWidgets('el camión avisa que es un piso y pinta el negativo', (tester) async {
      await _montar(tester);
      await _irA(tester, 'pestana_existencias');
      await tester.tap(find.byKey(const Key('almacen_CAMION_01')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('Lo vendido sin señal'), findsOneWidget);
      expect(find.byKey(const Key('aviso_negativos')), findsOneWidget);
      expect(find.text('-3 PZA'), findsOneWidget);
    });
  });

  group('entradas', () {
    testWidgets('una compra: abrir, capturar con costo, confirmar y ver la deuda',
        (tester) async {
      final servidor = await _montar(tester);
      await _irA(tester, 'pestana_entradas');
      await tester.tap(find.byKey(const Key('boton_nueva_entrada')));
      await tester.pumpAndSettle();

      // Compra es el motivo por omisión; el proveedor, del catálogo.
      await tester.tap(find.byKey(const Key('campo_proveedor_entrada')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Abarrotes del Centro').last);
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('campo_referencia_entrada')), 'F-100');
      await tester.tap(find.byKey(const Key('boton_abrir_entrada')));
      await tester.pumpAndSettle();

      expect(servidor.posts.first.$2, {
        'almacen_destino_id': 'b1', 'motivo': 'compra', 'proveedor_id': 'pr1',
        'proveedor': '', 'referencia': 'F-100', 'nota': '',
      });
      expect(find.byKey(const Key('pantalla_entrada')), findsOneWidget);

      await tester.tap(find.byKey(const Key('boton_agregar_a_la_entrada')));
      await tester.pumpAndSettle();
      // Para recibir se ofrece todo el catálogo, aunque no haya existencia.
      expect(servidor.pedidas.last.$2, {'almacen_id': 'b1'});
      await tester.tap(find.byKey(const Key('elegir_COCA600')));
      await tester.pumpAndSettle();

      // Sin costo, una compra no deja agregar.
      await tester.enterText(find.byKey(const Key('campo_cantidad_entrada')), '10');
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(find.byKey(const Key('boton_agregar_renglon_entrada')))
            .onPressed,
        isNull,
      );
      await tester.enterText(find.byKey(const Key('campo_costo_entrada')), '296');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('boton_agregar_renglon_entrada')));
      await tester.pumpAndSettle();

      expect(servidor.posts.last.$1, '/v1/almacen/entradas/e1/renglones');
      expect(servidor.posts.last.$2, {
        'sku': 'COCA600', 'unidad': 'CAJA', 'cantidad': '10', 'costo': '296', 'lote': '',
        'caducidad': '',
      });
      expect(textoQueContiene('10 CAJA = 240 PZA'), findsWidgets);

      await tester.tap(find.byKey(const Key('boton_confirmar_entrada')));
      await tester.pumpAndSettle();
      expect(textoQueContiene(r'Entran 240 piezas a Bodega, por $2,960.00'), findsOneWidget);
      await tester.tap(find.byKey(const Key('boton_confirmar_entrada_de_verdad')));
      await tester.pumpAndSettle();

      expect(servidor.posts.last.$1, '/v1/almacen/entradas/e1/confirmar');
      expect(find.byKey(const Key('aviso_cuenta_por_pagar')), findsOneWidget);
      expect(textoQueContiene(r'Cuenta por pagar a Abarrotes del Centro: $2,960.00'),
          findsOneWidget);
      // Confirmada ya no se edita.
      expect(find.byKey(const Key('boton_confirmar_entrada')), findsNothing);
      expect(find.byKey(const Key('boton_cancelar_entrada')), findsNothing);
    });

    testWidgets('el inventario inicial no se abre sin nota', (tester) async {
      final servidor = await _montar(tester);
      await _irA(tester, 'pestana_entradas');
      await tester.tap(find.byKey(const Key('boton_nueva_entrada')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('campo_motivo_entrada')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Inventario inicial').last);
      await tester.pumpAndSettle();

      // Sin proveedor: no se le compró a nadie.
      expect(find.byKey(const Key('campo_proveedor_entrada')), findsNothing);
      expect(
        tester.widget<FilledButton>(find.byKey(const Key('boton_abrir_entrada'))).onPressed,
        isNull,
      );
      await tester.enterText(find.byKey(const Key('campo_nota_entrada')), 'Conteo del arranque');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('boton_abrir_entrada')));
      await tester.pumpAndSettle();
      expect(servidor.posts.single.$2, {
        'almacen_destino_id': 'b1', 'motivo': 'inicial', 'proveedor': '', 'referencia': '',
        'nota': 'Conteo del arranque',
      });
    });
  });

  group('traspasos', () {
    testWidgets('de una bodega a otra: lo que hay en el origen, cuántos, y listo',
        (tester) async {
      final servidor = await _montar(tester);
      await _irA(tester, 'pestana_traspasos');
      await tester.tap(find.byKey(const Key('boton_nuevo_traspaso')));
      await tester.pumpAndSettle();

      // Solo lo que el origen tiene.
      expect(servidor.pedidas.last.$2, {'almacen_id': 'b1', 'solo_con_existencia': 'true'});
      expect(find.byKey(const Key('traspaso_producto_COCA600')), findsNothing);
      expect(textoQueContiene('Hay 760 PZA (31 CAJA + 16 PZA)'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('cantidad_traspaso_ATUN-140')), '2');
      await tester.enterText(find.byKey(const Key('campo_nota_traspaso')), 'Para el norte');
      await tester.tap(find.byKey(const Key('boton_traspasar')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('salen de Bodega y entran a Bodega Norte'), findsOneWidget);
      await tester.tap(find.byKey(const Key('boton_traspasar_de_verdad')));
      await tester.pumpAndSettle();

      expect(servidor.posts.single.$2, {
        'origen_id': 'b1', 'destino_id': 'b2',
        'renglones': [{'producto_id': 'p1', 'unidad': 'CAJA', 'cantidad': '2'}],
        'nota': 'Para el norte',
      });
      // De vuelta en la lista, con el mensaje del servidor y el traspaso nuevo.
      expect(find.byKey(const Key('pantalla_nuevo_traspaso')), findsNothing);
      expect(find.byKey(const Key('aviso_traspaso_hecho')), findsOneWidget);
      expect(find.byKey(const Key('traspaso_TR-000001')), findsOneWidget);
      expect(textoQueContiene('jueves 24 de septiembre, 12:05'), findsOneWidget);
    });

    testWidgets('si no alcanza, lo dice el servidor y no se sale de la pantalla',
        (tester) async {
      await _montar(tester, servidor: _ServidorDeAlmacen(noAlcanza: true));
      await _irA(tester, 'pestana_traspasos');
      await tester.tap(find.byKey(const Key('boton_nuevo_traspaso')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('cantidad_traspaso_ATUN-140')), '40');
      await tester.tap(find.byKey(const Key('boton_traspasar')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('boton_traspasar_de_verdad')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_nuevo_traspaso')), findsOneWidget);
      expect(textoQueContiene('No alcanza en Bodega'), findsOneWidget);
    });

    testWidgets('sin cantidades no se traspasa nada', (tester) async {
      final servidor = await _montar(tester);
      await _irA(tester, 'pestana_traspasos');
      await tester.tap(find.byKey(const Key('boton_nuevo_traspaso')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('boton_traspasar')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('aviso_traspaso_error')), findsOneWidget);
      expect(servidor.posts, isEmpty);
    });

    testWidgets('con una sola bodega lo explica y no ofrece traspasar', (tester) async {
      await _montar(tester, servidor: _ServidorDeAlmacen(bodegas: 1));
      await _irA(tester, 'pestana_traspasos');
      expect(find.byKey(const Key('aviso_una_bodega')), findsOneWidget);
      expect(find.byKey(const Key('boton_nuevo_traspaso')), findsNothing);
    });
  });
}
