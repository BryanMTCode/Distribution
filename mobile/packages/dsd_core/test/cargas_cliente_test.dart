/// El cliente de `/v1/cargas`: lo que la pantalla de la oficina recibe.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.codigo, this.cuerpo);

  final int codigo;
  final Object? cuerpo;
  final List<(String, Map<String, Object?>?)> pedidas = [];

  RespuestaHttp get _r => RespuestaHttp(codigo, jsonEncode(cuerpo));

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, null));
    return _r;
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    pedidas.add((ruta, cuerpo));
    return _r;
  }
}

final _detalle = {
  'id': 'c1',
  'folio': 'CG-000007',
  'estado': 'borrador',
  'fecha_operativa': '2026-10-07',
  'vendedor': 'Juan Pérez',
  'camion': 'Camión 01',
  'bodega': 'Bodega',
  'editable': true,
  'renglones': [
    {
      'id': 'r1',
      'producto_id': 'p1',
      'sku': 'ATUN-140',
      'nombre': 'Atún en agua 140 g',
      'unidad_base': 'PZA',
      'cantidad': '96.000',
      'en_bodega': '480.000',
    },
  ],
  'surtido': [
    {
      'producto_id': 'p1',
      'sku': 'ATUN-140',
      'nombre': 'Atún en agua 140 g',
      'unidad_base': 'PZA',
      'en_bodega': '480.000',
      'ya_en_la_carga': '96.000',
      'presentaciones': [
        {'unidad': 'CAJA', 'factor': '24.000'},
        {'unidad': 'PZA', 'factor': '1.000'},
      ],
      'por_omision': 'CAJA',
    },
  ],
  'bloqueos': ['«Moto G54» reportó 3 operación(es) sin subir.'],
  'mensaje': 'Se agregaron 1 producto(s).',
};

void main() {
  test('lee el detalle completo de una carga', () async {
    final c = await ClienteCargas(_Transporte(200, _detalle)).ver('c1');
    expect(c.folio, 'CG-000007');
    expect(c.renglones.single.cantidad, '96.000');
    expect(c.surtido.single.porOmision, 'CAJA');
    expect(c.surtido.single.presentaciones.map((p) => p.unidad), ['CAJA', 'PZA']);
    expect(c.bloqueos, hasLength(1));
    expect(c.mensaje, startsWith('Se agregaron'));
  });

  test('agregar manda la cantidad como texto, tal cual se tecleó', () async {
    final t = _Transporte(200, _detalle);
    await ClienteCargas(t).agregar('c1', const [
      PedidoDeCarga(productoId: 'p1', unidad: 'CAJA', cantidad: '4'),
    ]);
    expect(t.pedidas.single.$1, '/v1/cargas/c1/renglones');
    expect(t.pedidas.single.$2, {
      'renglones': [
        {'producto_id': 'p1', 'unidad': 'CAJA', 'cantidad': '4'},
      ],
    });
  });

  test('confirmar sin motivo no manda el campo', () async {
    final t = _Transporte(200, _detalle);
    await ClienteCargas(t).confirmar('c1');
    expect(t.pedidas.single.$2, isEmpty);
  });

  test('un 409 trae el texto del servidor, que dice qué hacer', () async {
    final t = _Transporte(409, {'detail': 'No se puede confirmar: sincroniza.'});
    expect(
      () => ClienteCargas(t).confirmar('c1'),
      throwsA(isA<CargaRechazada>()
          .having((e) => e.detalle, 'detalle', 'No se puede confirmar: sincroniza.')),
    );
  });

  test('un 403 es falta de permiso, no sesión vencida', () async {
    expect(
      () => ClienteCargas(_Transporte(403, {'detail': 'falta el permiso'})).abiertas(),
      throwsA(isA<SinPermisoDeCargar>()),
    );
    expect(
      () => ClienteCargas(_Transporte(401, {'detail': 'x'})).abiertas(),
      throwsA(isA<SesionInvalida>()),
    );
  });

  test('las cantidades se leen sin ceros de sobra', () {
    expect(cantidadLegible('96.000'), '96');
    expect(cantidadLegible('1.500'), '1.5');
    expect(cantidadLegible('12'), '12');
  });

  test('el gerente puede cargar; el vendedor no', () {
    const gerente = Perfil(usuarioId: 'g', codigo: 'G', nombre: 'G', rol: 'gerente',
        permisos: ['tablero.ver', 'inventario.cargar']);
    const vendedor = Perfil(usuarioId: 'v', codigo: 'V', nombre: 'V', rol: 'vendedor',
        permisos: ['ventas.crear']);
    const admin = Perfil(usuarioId: 'a', codigo: 'A', nombre: 'A', rol: 'admin', permisos: []);
    expect(gerente.puedeCargar, isTrue);
    expect(vendedor.puedeCargar, isFalse);
    expect(admin.puedeCargar, isTrue);
  });
}
