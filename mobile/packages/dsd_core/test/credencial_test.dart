/// Login sin señal, y el tercer contrato con el servidor.
///
/// Los hashes de `contracts/argon2_vectors.json` los generó el servidor con
/// `argon2-cffi`. Aquí los verifica una implementación de Argon2id en Dart
/// puro. Si los parámetros divergieran, el vendedor no podría entrar al
/// empezar el día —sin señal y sin forma de arreglarlo desde la calle—, que es
/// el peor momento posible para descubrirlo.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

final _documento = jsonDecode(
  File('../../../contracts/argon2_vectors.json').readAsStringSync(),
) as Map<String, dynamic>;

final _vectores = (_documento['vectores'] as List).cast<Map<String, dynamic>>();

CredencialLocal credencial({
  required String hash,
  DateTime? validaHasta,
  List<String> permisos = const ['ventas.crear'],
}) =>
    CredencialLocal(
      usuarioId: '019283a0-0001-7000-8000-000000000001',
      codigo: 'VEND01',
      nombre: 'Juan Pérez',
      rol: 'vendedor',
      passwordHash: hash,
      permisos: permisos,
      validaHasta: validaHasta ?? DateTime.utc(2026, 10, 1),
    );

void main() {
  final ahora = DateTime.utc(2026, 9, 24, 7);

  group('contrato de Argon2id con el servidor', () {
    test('los parámetros del archivo son los que espera el dispositivo', () {
      final p = _documento['parametros'] as Map<String, dynamic>;
      expect(p['variante'], equals('argon2id'));
      expect(p['memoria_kib'], equals(parametrosEsperados.memoriaKib));
      expect(p['iteraciones'], equals(parametrosEsperados.iteraciones));
      expect(p['paralelismo'], equals(parametrosEsperados.paralelismo));
    });

    for (final vector in _vectores) {
      final nombre = vector['nombre'] as String;
      final phc = vector['hash_phc'] as String;

      test('$nombre · el hash del servidor se verifica en Dart', () {
        final r = intentarLoginOffline(
          credencial(hash: phc),
          vector['password'] as String,
          ahora: ahora,
        );
        expect(r, equals(ResultadoLogin.ok));
      });

      test('$nombre · una contraseña incorrecta no entra', () {
        final r = intentarLoginOffline(
          credencial(hash: phc),
          vector['password_incorrecta'] as String,
          ahora: ahora,
        );
        expect(r, equals(ResultadoLogin.passwordIncorrecta));
      });
    }
  });

  group('vigencia', () {
    final phc = _vectores.first['hash_phc'] as String;
    final password = _vectores.first['password'] as String;

    test('pasada la vigencia exige conexión', () {
      // Un teléfono extraviado no debe seguir vendiendo indefinidamente.
      final r = intentarLoginOffline(
        credencial(hash: phc, validaHasta: DateTime.utc(2026, 9, 20)),
        password,
        ahora: ahora,
      );
      expect(r, equals(ResultadoLogin.credencialVencida));
    });

    test('la vigencia se revisa antes que la contraseña', () {
      // Así no se revela si el PIN era correcto en un equipo ya vencido.
      final r = intentarLoginOffline(
        credencial(hash: phc, validaHasta: DateTime.utc(2026, 9, 20)),
        'cualquier-cosa',
        ahora: ahora,
      );
      expect(r, equals(ResultadoLogin.credencialVencida));
    });

    test('avisa cuántos días quedan', () {
      final c = credencial(hash: phc, validaHasta: DateTime.utc(2026, 10, 1));
      expect(c.diasRestantes(ahora), equals(6));
    });

    test('sin credencial guardada no hay login offline', () {
      expect(
        intentarLoginOffline(null, password, ahora: ahora),
        equals(ResultadoLogin.sinCredencial),
      );
    });
  });

  group('hash aceptable', () {
    test('acepta el formato acordado', () {
      expect(hashAceptable(_vectores.first['hash_phc'] as String), isTrue);
    });

    test('rechaza parámetros más débiles que los pactados', () {
      // Un servidor mal configurado mandando un hash barato de romper.
      expect(
        hashAceptable(r'$argon2id$v=19$m=1024,t=1,p=1$c2FsdA$aGFzaA'),
        isFalse,
      );
    });

    test('rechaza otra variante de Argon2', () {
      expect(
        hashAceptable(r'$argon2i$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA'),
        isFalse,
      );
    });

    test('rechaza basura', () {
      for (final malo in ['', 'no-es-un-hash', r'$2b$12$abcdefgh']) {
        expect(hashAceptable(malo), isFalse, reason: malo);
      }
    });

    test('una credencial corrupta no deja entrar', () {
      final r = intentarLoginOffline(
        credencial(hash: 'basura'),
        'lo-que-sea',
        ahora: ahora,
      );
      expect(r, equals(ResultadoLogin.credencialCorrupta));
    });
  });

  group('permisos', () {
    final phc = _vectores.first['hash_phc'] as String;

    test('el vendedor tiene lo suyo y no más', () {
      final c = credencial(hash: phc, permisos: ['ventas.crear', 'clientes.crear']);
      expect(c.puede('ventas.crear'), isTrue);
      expect(c.puede('clientes.administrar'), isFalse);
    });

    test('la credencial se arma desde la respuesta del servidor', () {
      final c = CredencialLocal.deJson({
        'usuario_id': '019283a0-0001-7000-8000-000000000001',
        'codigo': 'VEND01',
        'nombre': 'Juan Pérez',
        'rol': 'vendedor',
        'password_hash': phc,
        'permisos': ['ventas.crear'],
        'almacen_id': '019283a0-0002-7000-8000-000000000002',
        'valida_hasta': '2026-10-01T00:00:00.000Z',
      });
      expect(c.codigo, equals('VEND01'));
      expect(c.validaHasta, equals(DateTime.utc(2026, 10, 1)));
      expect(c.puede('ventas.crear'), isTrue);
    });
  });
}
