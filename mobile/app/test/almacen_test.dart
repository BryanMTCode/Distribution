/// La pestaña Almacén del portal de la oficina: existencias, cargas y corte.
///
/// Entradas y Traspasos se quitaron de la app (ADR 0002 §134): «de la app
/// quitaré lo de entrada y traspasos». Se hacen en el panel.
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
  final List<(String, Map<String, String>?)> pedidas = [];

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
      _ => null,
    };
    if (cuerpo == null) return const RespuestaHttp(404, '{"detail":"Not Found"}');
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

const _todo = [
  'tablero.ver', 'inventario.ver', 'inventario.ajustar', 'inventario.cargar',
  'inventario.liquidar', 'mayoreo.vender',
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
    testWidgets('con todos los permisos: existencias, cargas y corte; ni mayoreo, ni '
        'entradas ni traspasos (ADR 0002 §134, §152)', (tester) async {
      await _montar(tester);
      expect(find.byKey(const Key('pantalla_almacen')), findsOneWidget);
      for (final k in ['pestana_existencias', 'pestana_cargas', 'pestana_cortes']) {
        expect(find.byKey(Key(k)), findsOneWidget, reason: k);
      }
      expect(find.byKey(const Key('pestana_entradas')), findsNothing);
      expect(find.byKey(const Key('pestana_traspasos')), findsNothing);
      // Mayoreo es una venta: tiene su propia pestaña abajo (ADR 0002 §152).
      expect(find.byKey(const Key('pestana_mayoreo')), findsNothing);
      expect(find.byKey(const Key('nav_mayoreo')), findsOneWidget);
      // El corte y la carga del vendedor van cada uno en su pestaña (ADR 0002 §82).
      expect(find.byKey(const Key('pestana_cierres')), findsNothing);
    });

    testWidgets('con solo ver, nada más las existencias', (tester) async {
      await _montar(tester, permisos: const ['tablero.ver', 'inventario.ver']);
      expect(find.byKey(const Key('pestana_cargas')), findsNothing);
      expect(find.byKey(const Key('pantalla_existencias')), findsOneWidget);
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
      // Y cada familia se pliega al tocarla (ADR 0002 §92).
      await tester.tap(find.byKey(const Key('familia_Abarrotes')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('existencia_ATUN-140')), findsNothing);
      expect(find.byKey(const Key('existencia_COCA600')), findsOneWidget);
      await tester.tap(find.byKey(const Key('familia_Abarrotes')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('existencia_ATUN-140')), findsOneWidget);

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
}
