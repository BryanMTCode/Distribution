/// La contraseña que la oficina cambió en el panel (ADR 0002 §136).
///
/// El teléfono guarda el hash de la contraseña de cuando se vinculó. Antes, la
/// nueva no entraba hasta volver a vincular el equipo. Ahora:
///
/// 1. **Con señal, la nueva entra** en cuanto se teclea: el teléfono se la
///    pregunta al servidor, guarda la credencial que manda y la vieja deja de
///    servir.
/// 2. **Sin señal no cambia nada**: el teléfono no tiene cómo saberla, y la
///    vieja sigue entrando.
/// 3. **Al renovar el acceso** —al sincronizar con señal— el teléfono trae la
///    credencial vigente, aunque el vendedor haya entrado con la vieja.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/repo_credencial.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// La contraseña que puso la oficina: la de otro vector del contrato, con su
/// hash real.
final _vectorNuevo = () {
  final documento = jsonDecode(
    File('../../contracts/argon2_vectors.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  return (documento['vectores'] as List)
      .cast<Map<String, dynamic>>()
      .firstWhere((v) => v['nombre'] == 'ascii_simple');
}();

String get _nueva => _vectorNuevo['password'] as String;

const _equipo = '019283a0-0003-7000-8000-000000000003';

/// Un servidor que ya tiene la contraseña nueva.
class _Servidor implements Transporte {
  _Servidor({this.sinSenal = false});

  final bool sinSenal;
  final List<Map<String, Object?>> logins = [];

  Map<String, Object?> get _respuesta => {
        'access_token': 'token-nuevo',
        'refresh_token': 'refresh-nuevo',
        'expira_en_seg': 900,
        'perfil': {
          'usuario_id': '019283a0-0001-7000-8000-000000000001',
          'codigo': 'VEND01',
          'nombre': 'Juan Pérez',
          'rol': 'vendedor',
          'permisos': ['ventas.crear'],
        },
        'credencial_local': {
          ...credencialDelServidor(validaHasta: '2027-01-31T00:00:00.000Z'),
          'password_hash': _vectorNuevo['hash_phc'],
        },
        'dispositivo_id': _equipo,
      };

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    if (sinSenal) throw const ErrorDeRed('sin señal');
    if (ruta == '/v1/auth/refresh') return RespuestaHttp(200, jsonEncode(_respuesta));
    logins.add(cuerpo);
    if (cuerpo['password'] != _nueva) {
      return const RespuestaHttp(401, '{"detail":"credenciales inválidas"}');
    }
    return RespuestaHttp(200, jsonEncode(_respuesta));
  }

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      const RespuestaHttp(200, '{}');
}

void _vinculado(BaseLocal base) => base.db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('dispositivo_id', ?)",
      [_equipo],
    );

Future<String?> _hashGuardado(AlmacenSeguroEnMemoria almacen) async =>
    (await RepoCredencial(almacen).leer())?.passwordHash;

void main() {
  testWidgets('con señal, la contraseña nueva entra y la vieja ya no', (tester) async {
    final almacen = AlmacenSeguroEnMemoria();
    final servidor = _Servidor();
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      almacenPropio: almacen,
      sembrar: _vinculado,
      extras: [transporteSinSesionProvider.overrideWithValue(servidor)],
    );

    await entrarCon(tester, _nueva);
    expect(find.text('Mi ruta'), findsOneWidget);
    // Se le preguntó al servidor con el código y el equipo de siempre.
    expect(servidor.logins.single['codigo'], 'VEND01');
    expect(servidor.logins.single['dispositivo_id'], _equipo);
    // Y la credencial guardada ya es la nueva, con su token para sincronizar.
    expect(await _hashGuardado(almacen), _vectorNuevo['hash_phc']);
    expect(await almacen.leer(claveRefreshToken), 'refresh-nuevo');

    await tocarEnElMenu(tester, const Key('boton_salir'));
    await entrarCon(tester, pinCorrecto); // la de antes
    expect(find.text('PIN incorrecto'), findsOneWidget);
    expect(find.text('Mi ruta'), findsNothing);
  });

  testWidgets('sin señal no cambia nada: la nueva no entra y la vieja sí',
      (tester) async {
    final almacen = AlmacenSeguroEnMemoria();
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      almacenPropio: almacen,
      sembrar: _vinculado,
      extras: [transporteSinSesionProvider.overrideWithValue(_Servidor(sinSenal: true))],
    );

    await entrarCon(tester, _nueva);
    expect(find.text('PIN incorrecto'), findsOneWidget);
    expect(await _hashGuardado(almacen), vectorArgon2['hash_phc']);

    await entrarCon(tester, pinCorrecto);
    expect(find.text('Mi ruta'), findsOneWidget);
  });

  test('al renovar el acceso, el teléfono trae la credencial vigente', () async {
    final almacen = AlmacenSeguroEnMemoria();
    await almacen.escribir('credencial_local_v1', jsonEncode(credencialDelServidor()));
    await almacen.escribir(claveRefreshToken, 'refresh-viejo');
    final elRef = Provider<Ref>((ref) => ref);
    final contenedor = ProviderContainer(overrides: [
      almacenSeguroProvider.overrideWithValue(almacen),
      transporteSinSesionProvider.overrideWithValue(_Servidor()),
    ]);
    addTearDown(contenedor.dispose);

    expect(await renovarAcceso(contenedor.read(elRef)), isTrue);
    expect(contenedor.read(tokenProvider), 'token-nuevo');
    expect(await _hashGuardado(almacen), _vectorNuevo['hash_phc']);
  });

  test('un teléfono sin credencial no gana una al renovar', () async {
    final almacen = AlmacenSeguroEnMemoria();
    await almacen.escribir(claveRefreshToken, 'refresh-viejo');
    final elRef = Provider<Ref>((ref) => ref);
    final contenedor = ProviderContainer(overrides: [
      almacenSeguroProvider.overrideWithValue(almacen),
      transporteSinSesionProvider.overrideWithValue(_Servidor()),
    ]);
    addTearDown(contenedor.dispose);

    expect(await renovarAcceso(contenedor.read(elRef)), isTrue);
    expect(await _hashGuardado(almacen), isNull);
  });
}
