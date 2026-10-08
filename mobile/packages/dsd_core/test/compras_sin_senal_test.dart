/// La compra del gerente sin señal (ADR 0002 §83).
///
/// · Se guarda sin señal, con su id, y se manda entera al tenerla.
/// · Sin señal no se pierde ni se marca: la siguiente vez se reintenta.
/// · Lo que el servidor rechaza se queda con su texto, y no se reintenta solo.
/// · El catálogo se guarda para poder capturar sin señal.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

class _Servidor implements Transporte {
  _Servidor(this.respuestas);

  /// Una respuesta por llamada, en orden. `null` = sin señal.
  final List<RespuestaHttp?> respuestas;
  final List<(String, Map<String, Object?>)> posts = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
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
    throw UnimplementedError(ruta);
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    final r = respuestas.removeAt(0);
    if (r == null) throw const ErrorDeRed('sin señal');
    posts.add((ruta, cuerpo));
    return r;
  }
}

RespuestaHttp _entrada(String folio, {String mensaje = 'EN confirmada.'}) => RespuestaHttp(
      200,
      jsonEncode({
        'id': 'x', 'folio': folio, 'motivo': 'compra', 'motivo_etiqueta': 'Compra',
        'estado': 'confirmada', 'editable': false, 'exige_costo': true,
        'proveedor': null, 'referencia': null, 'nota': null,
        'fecha_operativa': '2026-10-07', 'bodega': 'Bodega', 'almacen_destino_id': 'b1',
        'renglones': [], 'total_piezas': '24.000', 'importe_capturado': '0.00',
        'sin_costo': 1, 'cuenta': null, 'mensaje': mensaje,
      }),
    );

void main() {
  late Database db;
  late ComprasSinSenal compras;
  var n = 0;

  setUp(() {
    n = 0;
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    compras = ComprasSinSenal(
      db,
      nuevoUuid: () => 'compra-${++n}',
      ahora: () => DateTime(2026, 10, 7, 16, 45),
    );
  });

  tearDown(() => db.dispose());

  RenglonDeCompra cocas(int cajas, {String? costo}) => RenglonDeCompra(
        productoId: 'p1',
        nombre: 'Coca 600',
        unidad: 'CAJA',
        factor: Factor.deEnteros(24),
        bultos: cajas,
        costoPorBulto: costo == null ? null : Dinero.deTexto(costo),
      );

  test('se guarda sin señal, con el día en que llegó', () {
    final c = compras.capturar(
      renglones: [cocas(10, costo: '296.00'), cocas(0)],
      proveedorId: 'pr1',
      referencia: ' R-77 ',
    );
    expect(c.pendiente, isTrue);
    expect(c.compra.fecha, '2026-10-07');
    expect(c.compra.referencia, 'R-77');
    // El renglón en cero no viaja.
    expect(c.compra.renglones, hasLength(1));
    expect(c.compra.renglones.single.piezas, Cantidad.deEnteros(240));
    expect(c.compra.importeConCosto, Dinero.deTexto('2960.00'));
    expect(compras.pendientes, 1);
  });

  test('sin renglones o con costo cero no se guarda', () {
    expect(() => compras.capturar(renglones: [cocas(0)]), throwsA(isA<MotivoNoCompra>()));
    expect(
      () => compras.capturar(renglones: [cocas(1, costo: '0.00')]),
      throwsA(isA<MotivoNoCompra>()),
    );
    expect(compras.pendientes, 0);
  });

  test('al tener señal se manda entera, con su id y el costo opcional', () async {
    compras.capturar(renglones: [cocas(10, costo: '296.00')]);
    compras.capturar(renglones: [cocas(2)]);
    final servidor = _Servidor([_entrada('EN-000001'), _entrada('EN-000002')]);

    final r = await compras.enviar(ClienteAlmacen(servidor));
    expect(r.enviadas, 2);
    expect(compras.pendientes, 0);
    final (ruta, cuerpo) = servidor.posts.first;
    expect(ruta, '/v1/almacen/compras');
    expect(cuerpo['id'], 'compra-1');
    expect(cuerpo['fecha'], '2026-10-07');
    expect((cuerpo['renglones']! as List).single, {
      'producto_id': 'p1', 'nombre': 'Coca 600', 'unidad': 'CAJA',
      'factor': '24.0000', 'cantidad': '10', 'costo': '296.00',
    });
    // La segunda va sin costo: solo suma inventario.
    expect(((servidor.posts.last.$2['renglones']! as List).single as Map)['costo'], '');
    expect(compras.todas().map((c) => c.folio), ['EN-000002', 'EN-000001']);
  });

  test('sin señal no se pierde ni se marca; la siguiente vez se manda', () async {
    compras.capturar(renglones: [cocas(1)]);
    final r = await compras.enviar(ClienteAlmacen(_Servidor([null])));
    expect(r.sinSenal, isTrue);
    expect(compras.pendientes, 1);

    final otra = await compras.enviar(ClienteAlmacen(_Servidor([_entrada('EN-000009')])));
    expect(otra.enviadas, 1);
    expect(compras.pendientes, 0);
  });

  test('lo que el servidor rechaza se queda con su texto; se puede reintentar', () async {
    compras.capturar(renglones: [cocas(1)]);
    final r = await compras.enviar(ClienteAlmacen(_Servidor([
      const RespuestaHttp(409, '{"detail":"Ese proveedor no existe o está inactivo."}'),
    ])));
    expect(r.rechazadas, 1);
    final c = compras.todas().single;
    expect(c.rechazada, isTrue);
    expect(c.mensaje, 'Ese proveedor no existe o está inactivo.');
    // No se reintenta sola.
    expect(compras.pendientes, 0);

    compras.reintentar(c.compra.id);
    expect(compras.pendientes, 1);
  });

  test('el catálogo se guarda para capturar sin señal', () async {
    expect(compras.catalogoGuardado(), isNull);
    final catalogo = await ClienteAlmacen(_Servidor([])).catalogoDeCompras();
    compras.guardarCatalogo(catalogo);
    final (guardado, cuando) = compras.catalogoGuardado()!;
    expect(guardado.productos.single.porOmision.$1, 'CAJA');
    expect(guardado.productos.single.porOmision.$2, Factor.deEnteros(24));
    expect(guardado.proveedores.single.nombre, 'Abarrotes del Centro');
    expect(cuando, DateTime(2026, 10, 7, 16, 45));
  });
}
