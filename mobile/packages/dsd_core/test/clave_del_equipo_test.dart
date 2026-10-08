/// La clave corta para vincular el teléfono (ADR 0002 §85).
///
/// · Lo que se teclea puede ser la clave que eligió la oficina o, como antes,
///   el id largo: se distinguen por la forma.
/// · Con la clave, el login la manda como `clave_equipo` y el teléfono se
///   entera de su id por la respuesta.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Servidor implements Transporte {
  Map<String, Object?>? cuerpo;

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      const RespuestaHttp(405, '{}');

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    this.cuerpo = cuerpo;
    return RespuestaHttp(200, jsonEncode({
      'access_token': 'a', 'refresh_token': 'r', 'expira_en_seg': 900,
      'perfil': null, 'credencial_local': null,
      'dispositivo_id': '0198a2f4-0000-7000-8000-000000000001',
    }));
  }
}

void main() {
  test('el id largo se reconoce por su forma; lo demás es una clave', () {
    expect(pareceIdDeEquipo(' 0198A2F4-0000-7000-8000-000000000001 '), isTrue);
    expect(pareceIdDeEquipo('RUTA4'), isFalse);
    expect(pareceIdDeEquipo('BRYAN-1'), isFalse);
  });

  test('con la clave, el login la manda y se entera del id', () async {
    final servidor = _Servidor();
    final sesion = await ClienteAuth(servidor)
        .entrar(codigo: 'VEND01', password: 'x', claveEquipo: 'RUTA4');
    expect(servidor.cuerpo, {'codigo': 'VEND01', 'password': 'x', 'clave_equipo': 'RUTA4'});
    expect(sesion.dispositivoId, '0198a2f4-0000-7000-8000-000000000001');
  });
}
