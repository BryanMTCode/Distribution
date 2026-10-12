/// El cliente de `/v1/objetivos/avance`: la barra del Tablero por la vista que
/// se escoja (ADR 0002 §116).
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.codigo, this.cuerpo);

  final int codigo;
  final Object? cuerpo;
  final List<(String, Map<String, String>?)> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    return RespuestaHttp(codigo, jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      const RespuestaHttp(405, '{}');
}

void main() {
  test('la semana: cada vendedor y la empresa como la suma', () async {
    final t = _Transporte(200, {
      'vista': 'semana', 'desde': '2026-10-12', 'hasta': '2026-10-18',
      'empresa': {
        'ruta_id': null, 'ruta': null, 'objetivo': '21000.00', 'venta': '3500.00',
        'logrado': '16.7', 'esperado': '16.7', 'semaforo': 'adelante', 'falta': '17500.00',
      },
      'rutas': [
        {'ruta_id': 'r1', 'ruta': 'R1101 · Ruta 1101', 'objetivo': '21000.00',
         'venta': '3500.00', 'logrado': '16.7', 'esperado': '16.7', 'semaforo': 'adelante',
         'falta': '17500.00'},
        {'ruta_id': 'r2', 'ruta': 'R1102 · Ruta 1102', 'objetivo': null, 'venta': '0.00',
         'logrado': null, 'esperado': null, 'semaforo': 'sin_objetivo', 'falta': '0.00'},
      ],
      'mayoreo': '0.00',
    });
    final a = await ClienteObjetivos(t).avance('semana', fecha: '2026-10-12');
    expect(t.pedidas.single.$1, '/v1/objetivos/avance');
    expect(t.pedidas.single.$2, {'vista': 'semana', 'fecha': '2026-10-12'});
    expect((a.vista, a.desde, a.hasta), ('semana', '2026-10-12', '2026-10-18'));
    expect(a.empresa.ruta, isNull);
    expect(a.empresa.objetivo, '21000.00');
    expect(a.rutas.map((r) => r.sinObjetivo), [false, true]);
  });

  test('un servidor anterior no tiene la vista: se dice', () async {
    final t = _Transporte(404, {'detail': 'Not Found'});
    expect(() => ClienteObjetivos(t).avance('hoy'), throwsA(isA<Exception>()));
  });
}
