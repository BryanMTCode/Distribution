/// El cliente de `/v1/panel`: los botones del dashboard en la app de la oficina
/// y el boleto para abrirlos (ADR 0002 §194).
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.codigo, this.cuerpo);

  final int codigo;
  final Object? cuerpo;
  final List<String> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add('GET $ruta');
    return RespuestaHttp(codigo, jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    pedidas.add('POST $ruta');
    return RespuestaHttp(codigo, jsonEncode(this.cuerpo));
  }
}

void main() {
  test('los botones son los que dice el servidor', () async {
    final t = _Transporte(200, [
      {'titulo': 'Mover mercancía', 'descripcion': 'De la bodega a un camión',
       'ruta': '/panel/inventario/mover', 'icono': 'mover'},
      {'titulo': 'Uno nuevo', 'ruta': '/panel/otra'},
    ]);
    final atajos = await ClienteDelPanel(t).atajos();
    expect(t.pedidas, ['GET /v1/panel/atajos']);
    expect(atajos.map((a) => (a.titulo, a.ruta, a.icono)), [
      ('Mover mercancía', '/panel/inventario/mover', 'mover'),
      ('Uno nuevo', '/panel/otra', ''),
    ]);
    expect(atajos.last.descripcion, '');
  });

  test('a quien no le tocan, ninguno', () async {
    expect(await ClienteDelPanel(_Transporte(200, [])).atajos(), isEmpty);
  });

  test('el boleto y dónde se canjea', () async {
    final t = _Transporte(200, {'boleto': 'abc', 'expira_en': '2026-10-31T12:00:00Z',
                                'entrar': '/panel/entrar/app'});
    final b = await ClienteDelPanel(t).boleto();
    expect(t.pedidas, ['POST /v1/panel/boleto']);
    expect((b.boleto, b.entrar), ('abc', '/panel/entrar/app'));
  });

  test('sin permiso, o un servidor que todavía no lo tiene, se dice', () async {
    expect(() => ClienteDelPanel(_Transporte(403, {'detail': 'no'})).boleto(),
        throwsA(isA<SinPermisoDeLaOficina>()));
    expect(() => ClienteDelPanel(_Transporte(404, {'detail': 'Not Found'})).atajos(),
        throwsA(isA<Exception>()));
  });
}
