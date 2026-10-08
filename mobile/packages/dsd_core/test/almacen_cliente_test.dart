/// El cliente de `/v1/almacen`: existencias, entradas y traspasos.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.codigo, this.cuerpo);

  final int codigo;
  final Object? cuerpo;
  final List<(String, Object?)> pedidas = [];

  RespuestaHttp get _r =>
      RespuestaHttp(codigo, cuerpo is String ? cuerpo! as String : jsonEncode(cuerpo));

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    return _r;
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    pedidas.add((ruta, cuerpo));
    return _r;
  }
}

final _bodega = {
  'id': 'b1', 'codigo': 'BODEGA_PRINCIPAL', 'nombre': 'Bodega', 'tipo': 'bodega',
  'responsable': null, 'productos': 1, 'piezas': '760.000', 'negativos': 0,
};

final _entrada = {
  'id': 'e1', 'folio': 'EN-000001', 'motivo': 'compra', 'motivo_etiqueta': 'Compra a proveedor',
  'estado': 'confirmada', 'editable': false, 'exige_costo': true,
  'proveedor': 'Abarrotes del Centro', 'referencia': 'F-100', 'nota': null,
  'fecha_operativa': '2026-10-08', 'bodega': 'Bodega', 'almacen_destino_id': 'b1',
  'renglones': [
    {'id': 'r1', 'sku': 'COCA600', 'nombre': 'Coca 600', 'unidad_base': 'PZA',
     'cantidad': '240.000', 'unidad_codigo': 'CAJA', 'unidades_capturadas': '10.000',
     'costo_unitario': '12.3333', 'importe': '2960.00', 'lote': null,
     'existencia': '0.000', 'proyectado': '240.000'},
  ],
  'total_piezas': '240.000', 'importe_capturado': '2960.00', 'sin_costo': 0,
  'cuenta': {'proveedor': 'Abarrotes del Centro', 'importe_original': '2960.00',
             'saldo': '2960.00', 'estado': 'pendiente', 'fecha_vencimiento': '2026-11-07'},
  'mensaje': 'Entrada confirmada.',
};

void main() {
  group('existencias', () {
    test('lee los almacenes y lo que hay en cada uno', () async {
      final [b] = await ClienteAlmacen(_Transporte(200, [_bodega])).almacenes();
      expect(b.esBodega, isTrue);
      expect(b.piezas, '760.000');
      expect(b.responsable, isNull);

      final t = _Transporte(200, {
        'almacen': _bodega,
        'existencias': [
          {'producto_id': 'p1', 'sku': 'ATUN', 'nombre': 'Atún', 'unidad_base': 'PZA',
           'cantidad': '-3.000',
           'presentaciones': [{'unidad': 'CAJA', 'factor': '24.000'},
                              {'unidad': 'PZA', 'factor': '1.000'}]},
        ],
      });
      final e = await ClienteAlmacen(t).existencias('b1', busqueda: ' atun ');
      expect(e.existencias.single.negativa, isTrue);
      expect(e.existencias.single.presentaciones.first.unidad, 'CAJA');
      expect(t.pedidas.single.$1, '/v1/almacen/b1/existencias');
      expect(t.pedidas.single.$2, {'q': 'atun'});
    });

    test('para traspasar pide solo lo que hay en el origen', () async {
      final t = _Transporte(200, <Object?>[]);
      await ClienteAlmacen(t).productos('b1', soloConExistencia: true);
      await ClienteAlmacen(t).productos('b1');
      expect(t.pedidas[0].$2, {'almacen_id': 'b1', 'solo_con_existencia': 'true'});
      expect(t.pedidas[1].$2, {'almacen_id': 'b1'});
    });
  });

  group('entradas', () {
    test('lee la entrada con su cuenta por pagar', () async {
      final e = await ClienteAlmacen(_Transporte(200, _entrada)).verEntrada('e1');
      expect(e.editable, isFalse);
      expect(e.cuenta!.saldo.centavos, 296000);
      expect(e.renglones.single.bultos, '10.000');
      expect(e.renglones.single.importe!.centavos, 296000);
      expect(e.importeCapturado.centavos, 296000);
    });

    test('los motivos dicen qué exigen', () async {
      final l = ListaDeEntradas.deJson({
        'entradas': <Object?>[], 'bodegas': <Object?>[], 'proveedores': <Object?>[],
        'motivos': [
          {'clave': 'compra', 'etiqueta': 'Compra', 'explicacion': ''},
          {'clave': 'inicial', 'etiqueta': 'Inicial', 'explicacion': ''},
        ],
      });
      expect([for (final m in l.motivos) (m.exigeCosto, m.exigeNota)],
          [(true, false), (false, true)]);
    });

    test('abrir y capturar viajan como los pide el servidor', () async {
      final t = _Transporte(200, _entrada);
      final c = ClienteAlmacen(t);
      await c.abrirEntrada(const NuevaEntrada(bodegaId: 'b1', motivo: 'ajuste'));
      await c.agregarRenglon(
        'e1',
        const RenglonPorRecibir(sku: 'COCA600', unidad: 'CAJA', cantidad: '10', costo: '296'),
      );
      await c.cancelarEntrada('e1', motivo: 'Llegó mal');
      expect(t.pedidas[0].$1, '/v1/almacen/entradas');
      expect(t.pedidas[0].$2, {
        'almacen_destino_id': 'b1', 'motivo': 'ajuste', 'proveedor': '', 'referencia': '',
        'nota': '',
      });
      expect(t.pedidas[1].$1, '/v1/almacen/entradas/e1/renglones');
      expect((t.pedidas[1].$2! as Map)['costo'], '296');
      expect(t.pedidas[2].$2, {'motivo': 'Llegó mal'});
    });

    test('un 409 trae el texto del servidor; un 403 es permiso', () async {
      expect(
        () => ClienteAlmacen(_Transporte(409, {'detail': 'El costo es obligatorio'}))
            .confirmarEntrada('e1'),
        throwsA(isA<CargaRechazada>()
            .having((e) => e.detalle, 'detalle', 'El costo es obligatorio')),
      );
      expect(
        () => ClienteAlmacen(_Transporte(403, {'detail': 'falta'})).entradas(),
        throwsA(isA<SinPermisoDeAlmacen>()),
      );
      expect(
        () => ClienteAlmacen(_Transporte(404, {'detail': 'Not Found'})).almacenes(),
        throwsA(isA<ServidorSinEstaFuncion>()),
      );
    });
  });

  group('traspasos', () {
    test('el traspaso viaja con sus renglones y devuelve su folio', () async {
      final t = _Transporte(200, {'id': 't1', 'folio': 'TR-000001', 'mensaje': 'Listo'});
      final hecho = await ClienteAlmacen(t).traspasar(
        origenId: 'b1',
        destinoId: 'b2',
        renglones: const [RenglonPorTraspasar(productoId: 'p1', unidad: 'CAJA', cantidad: '2')],
        nota: 'Para el norte',
      );
      expect(hecho.folio, 'TR-000001');
      expect(t.pedidas.single.$1, '/v1/almacen/traspasos');
      expect(t.pedidas.single.$2, {
        'origen_id': 'b1', 'destino_id': 'b2',
        'renglones': [{'producto_id': 'p1', 'unidad': 'CAJA', 'cantidad': '2'}],
        'nota': 'Para el norte',
      });
    });

    test('la lista trae los traspasos y las bodegas', () async {
      final l = await ClienteAlmacen(_Transporte(200, {
        'traspasos': [
          {'id': 't1', 'folio': 'TR-000001', 'creado_en': '2026-10-08T17:00:00Z',
           'origen': 'Bodega', 'destino': 'Norte', 'quien': 'Bryan', 'renglones': 1,
           'piezas': '60.000', 'observaciones': null},
        ],
        'bodegas': [{'id': 'b1', 'codigo': 'B', 'nombre': 'Bodega'}],
      })).traspasos();
      expect(l.traspasos.single.creadoEn.isUtc, isTrue);
      expect(l.traspasos.single.piezas, '60.000');
      expect(l.bodegas.single.nombre, 'Bodega');
    });
  });
}
