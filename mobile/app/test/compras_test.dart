/// Recibir una compra de proveedor desde el teléfono del gerente (ADR 0002 §83).
///
/// · Sin señal: se captura con el catálogo guardado, se queda en el teléfono y
///   se ve como «por mandar».
/// · Con señal: se manda entera, con su id, y se ve con el folio con que entró.
/// · El costo es opcional: el renglón sin costo viaja vacío.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

class _Gerencia extends ControladorSesion {
  @override
  Sesion build() => const SesionDeGerencia(
        Perfil(usuarioId: 'g1', codigo: 'GER01', nombre: 'Bryan', rol: 'gerente',
            permisos: ['tablero.ver', 'inventario.ver', 'inventario.ajustar']),
      );
}

class _Servidor implements Transporte {
  _Servidor({required this.conSenal});

  bool conSenal;
  final List<(String, Map<String, Object?>)> posts = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    if (!conSenal) throw const ErrorDeRed('sin señal');
    if (ruta == '/v1/almacen/entradas') {
      return RespuestaHttp(200, jsonEncode({
        'entradas': [],
        'bodegas': [
          {'id': 'b1', 'codigo': 'BODEGA_PRINCIPAL', 'nombre': 'Bodega'},
        ],
        'proveedores': [
          {'id': 'pr1', 'codigo': 'ABC', 'nombre': 'Abarrotes del Centro'},
        ],
        'motivos': [],
      }));
    }
    if (ruta == '/v1/almacen/productos') {
      return RespuestaHttp(200, jsonEncode([
        {'producto_id': 'p1', 'sku': 'COCA600', 'nombre': 'Coca 600', 'unidad_base': 'PZA',
         'maneja_lote': false, 'existencia': '0.000',
         'presentaciones': [
           {'unidad': 'CAJA', 'factor': '24.000'},
           {'unidad': 'PZA', 'factor': '1.000'},
         ]},
      ]));
    }
    return const RespuestaHttp(503, '{"detail":"no importa aquí"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    if (!conSenal) throw const ErrorDeRed('sin señal');
    posts.add((ruta, cuerpo));
    return RespuestaHttp(200, jsonEncode({
      'id': cuerpo['id'], 'folio': 'EN-000041', 'motivo': 'compra',
      'motivo_etiqueta': 'Compra a proveedor', 'estado': 'confirmada', 'editable': false,
      'exige_costo': true, 'proveedor': 'Abarrotes del Centro', 'referencia': null,
      'nota': null, 'fecha_operativa': '2026-09-24', 'bodega': 'Bodega',
      'almacen_destino_id': 'b1', 'renglones': [], 'total_piezas': '240.000',
      'importe_capturado': '0.00', 'sin_costo': 1, 'cuenta': null,
      'mensaje': 'EN-000041 confirmada: 240 piezas en la bodega.',
    }));
  }
}

Future<_Servidor> _montar(WidgetTester tester, {required bool conSenal, bool conCopia = true}) async {
  final servidor = _Servidor(conSenal: conSenal);
  await montarApp(
    tester,
    sembrar: (base) {
      if (!conCopia) return;
      // La copia que dejó la última vez que hubo señal.
      ComprasSinSenal(base.db, nuevoUuid: () => 'x', ahora: relojDePrueba).guardarCatalogo(
        CatalogoDeCompras(
          productos: [
            ProductoParaComprar(
              productoId: 'p1', sku: 'COCA600', nombre: 'Coca 600', unidadBase: 'PZA',
              presentaciones: [('PZA', Factor.uno), ('CAJA', Factor.deEnteros(24))],
            ),
          ],
          proveedores: const [OpcionDeAlmacen(id: 'pr1', nombre: 'Abarrotes del Centro')],
        ),
      );
    },
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(servidor),
      sesionProvider.overrideWith(_Gerencia.new),
    ],
  );
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('nav_almacen')));
  await tester.pumpAndSettle();
  await tester.ensureVisible(find.byKey(const Key('pestana_entradas')));
  await tester.tap(find.byKey(const Key('pestana_entradas')));
  await tester.pumpAndSettle();
  return servidor;
}

Future<void> _capturar(WidgetTester tester, {String costo = ''}) async {
  await tester.tap(find.byKey(const Key('boton_recibir_compra')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('boton_agregar_producto_compra')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const Key('producto_compra_COCA600')));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(const Key('bultos_COCA600')), '10');
  if (costo.isNotEmpty) {
    await tester.enterText(find.byKey(const Key('costo_COCA600')), costo);
  }
  await tester.ensureVisible(find.byKey(const Key('boton_guardar_compra')));
  await tester.tap(find.byKey(const Key('boton_guardar_compra')));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('sin señal se captura con la copia y se queda por mandar', (tester) async {
    final servidor = await _montar(tester, conSenal: false);
    // La tarjeta se ve aunque la lista del servidor no.
    expect(find.byKey(const Key('tarjeta_compras_telefono')), findsOneWidget);
    await tester.tap(find.byKey(const Key('boton_recibir_compra')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('Sin señal: se usa el catálogo guardado'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();

    await _capturar(tester);
    expect(textoQueContiene('Compra guardada en el teléfono'), findsOneWidget);
    expect(find.byKey(const Key('compras_por_mandar')), findsOneWidget);
    expect(servidor.posts, isEmpty);

    // Llega la señal: se manda con el botón.
    servidor.conSenal = true;
    await tester.tap(find.byKey(const Key('boton_enviar_compras')));
    await tester.pumpAndSettle();
    expect(servidor.posts.single.$1, '/v1/almacen/compras');
    expect(find.byKey(const Key('compras_por_mandar')), findsNothing);
    expect(textoQueContiene('Entró: EN-000041'), findsOneWidget);
  });

  testWidgets('con señal entra de una vez; el costo vacío viaja vacío', (tester) async {
    final servidor = await _montar(tester, conSenal: true);
    await _capturar(tester);
    final (ruta, cuerpo) = servidor.posts.single;
    expect(ruta, '/v1/almacen/compras');
    expect(cuerpo['fecha'], '2026-09-24');
    final renglon = (cuerpo['renglones']! as List).single as Map;
    expect(renglon['unidad'], 'CAJA');
    expect(renglon['cantidad'], '10');
    expect(renglon['costo'], '');
    expect(textoQueContiene('EN-000041 confirmada'), findsOneWidget);
  });

  testWidgets('sin señal y sin copia, dice que hace falta señal una vez', (tester) async {
    await _montar(tester, conSenal: false, conCopia: false);
    await tester.tap(find.byKey(const Key('boton_recibir_compra')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('hace falta haber tenido señal una vez'), findsOneWidget);
  });
}
