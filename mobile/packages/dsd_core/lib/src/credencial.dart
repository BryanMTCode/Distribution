/// Login sin señal.
///
/// El vendedor arranca su día en la bodega, muchas veces sin datos. Si el
/// login exigiera internet, no podría ni abrir la app para ver su carga.
///
/// El servidor manda su **propio hash Argon2id** en la respuesta del login
/// (`credencial_local`), y el teléfono lo guarda cifrado. Desde ahí verifica el
/// PIN localmente, sin red. No se guarda la contraseña: solo el hash, que es
/// el mismo que está en la base de datos del servidor.
///
/// ─────────────────────────────────────────────────────────────────────────
/// VIGENCIA: LO QUE EVITA QUE UN EQUIPO PERDIDO OPERE PARA SIEMPRE
/// ─────────────────────────────────────────────────────────────────────────
/// La credencial trae `validaHasta`. Pasada esa fecha el login exige conexión,
/// que es cuando el servidor puede decir que el dispositivo está revocado. Sin
/// vigencia, un teléfono extraviado seguiría vendiendo indefinidamente con la
/// cartera completa dentro.
///
/// El reloj del dispositivo es manipulable: adelantarlo no da acceso extra
/// —solo acerca el vencimiento— y atrasarlo para revivir una credencial
/// vencida se detecta al sincronizar, porque el servidor compara sus fechas.
library;

import 'dart:convert';

import 'package:hashlib/hashlib.dart';

/// Parámetros de Argon2id. Contrato con el servidor: ver contracts/README.md §2.
///
/// No se usan para verificar —el PHC los trae embebidos— sino para comprobar
/// que el hash recibido se generó con lo acordado. Un hash con parámetros más
/// flojos que los pactados no se acepta.
class ParametrosArgon2 {
  const ParametrosArgon2({
    this.memoriaKib = 65536,
    this.iteraciones = 3,
    this.paralelismo = 4,
  });

  final int memoriaKib;
  final int iteraciones;
  final int paralelismo;
}

const parametrosEsperados = ParametrosArgon2();

enum ResultadoLogin {
  /// Entra.
  ok,

  /// PIN incorrecto.
  passwordIncorrecta,

  /// Pasó `validaHasta`: hay que conectarse para renovar.
  credencialVencida,

  /// Nunca se guardó credencial en este equipo.
  sinCredencial,

  /// El hash no viene en el formato acordado, o con parámetros más débiles.
  credencialCorrupta,
}

/// Lo que el teléfono guarda para poder autenticar sin señal.
class CredencialLocal {
  const CredencialLocal({
    required this.usuarioId,
    required this.codigo,
    required this.nombre,
    required this.rol,
    required this.passwordHash,
    required this.permisos,
    required this.validaHasta,
    this.almacenId,
  });

  /// Desde la respuesta de `POST /v1/auth/login`.
  factory CredencialLocal.deJson(Map<String, Object?> json) => CredencialLocal(
        usuarioId: json['usuario_id']! as String,
        codigo: json['codigo']! as String,
        nombre: json['nombre']! as String,
        rol: json['rol']! as String,
        passwordHash: json['password_hash']! as String,
        permisos: ((json['permisos'] ?? const <Object?>[]) as List).cast<String>(),
        almacenId: json['almacen_id'] as String?,
        validaHasta: DateTime.parse(json['valida_hasta']! as String).toUtc(),
      );

  final String usuarioId;

  /// 'VEND01'. Es también el prefijo del folio impreso.
  final String codigo;
  final String nombre;
  final String rol;

  /// Cadena PHC de Argon2id, la misma que el servidor guarda.
  final String passwordHash;
  final List<String> permisos;
  final String? almacenId;
  final DateTime validaHasta;

  bool puede(String permiso) => rol == 'admin' || permisos.contains(permiso);

  bool vencidaEn(DateTime ahora) => ahora.toUtc().isAfter(validaHasta);

  /// Cuántos días quedan antes de exigir conexión. Se muestra al vendedor para
  /// que no lo tome por sorpresa a media ruta.
  int diasRestantes(DateTime ahora) =>
      validaHasta.difference(ahora.toUtc()).inDays;
}

/// Verifica que el hash venga en el formato acordado y sin parámetros débiles.
///
/// Sin esta comprobación, un servidor comprometido —o una configuración mal
/// puesta— podría mandar un hash barato de romper y el teléfono lo aceptaría
/// sin chistar.
bool hashAceptable(String phc, {ParametrosArgon2 esperados = parametrosEsperados}) {
  final m = RegExp(r'^\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$').firstMatch(phc);
  if (m == null) return false;
  final memoria = int.parse(m.group(1)!);
  final iteraciones = int.parse(m.group(2)!);
  final paralelismo = int.parse(m.group(3)!);
  return memoria >= esperados.memoriaKib &&
      iteraciones >= esperados.iteraciones &&
      paralelismo >= esperados.paralelismo;
}

/// Intenta el login contra la credencial guardada.
///
/// Verificar cuesta cientos de milisegundos a propósito: es el costo que hace
/// caro probar PINs por fuerza bruta contra un teléfono robado. No se agrega un
/// contador de intentos en la app porque quien tiene el equipo tiene el hash y
/// puede atacarlo sin pasar por la app; la defensa real es el costo de Argon2
/// y la vigencia de la credencial.
ResultadoLogin intentarLoginOffline(
  CredencialLocal? credencial,
  String password, {
  required DateTime ahora,
}) {
  if (credencial == null) return ResultadoLogin.sinCredencial;
  if (!hashAceptable(credencial.passwordHash)) return ResultadoLogin.credencialCorrupta;
  if (credencial.vencidaEn(ahora)) return ResultadoLogin.credencialVencida;

  final coincide = argon2Verify(credencial.passwordHash, utf8.encode(password));
  return coincide ? ResultadoLogin.ok : ResultadoLogin.passwordIncorrecta;
}
