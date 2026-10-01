/// Login contra el servidor.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ HACÍA FALTA, Y POR QUÉ NO EXISTÍA ANTES
/// ─────────────────────────────────────────────────────────────────────────
/// El vendedor entra **sin señal**: su credencial —el hash Argon2id— se guarda
/// en el Keystore la primera vez y el login posterior se verifica en el
/// teléfono. Ese es el camino normal y no necesita este archivo.
///
/// El de Gerencia es el opuesto. Un gerente no tiene camión ni equipo
/// registrado, y su teléfono **no guarda cartera**: lo correcto es que tampoco
/// guarde una credencial que le permita entrar sin red. Y además no le serviría
/// de nada: el tablero existe para ver lo que están haciendo los otros, y eso
/// no se puede saber sin preguntarle al servidor.
///
/// Así que el perfil de Gerencia entra en línea, con código y contraseña, cada
/// vez que la sesión guardada caduca. Lo que sí se guarda es el **refresh
/// token**, para que abrir la app por la mañana no exija teclear la contraseña
/// otra vez.
///
/// El servidor ya impedía por su cuenta que un vendedor use este camino: un
/// login de rol 'vendedor' sin `dispositivo_id` se rechaza con 400. No es una
/// validación que el cliente pueda relajar.
library;

import 'dart:convert';

import 'credencial.dart';
import 'sync_cliente.dart' show ServidorConProblemas;
import 'transporte.dart';

/// Quién entró. Lo manda el servidor para que la app sepa qué dibujar.
///
/// Los permisos sirven para decidir qué pantallas mostrar, **nunca** para
/// decidir qué datos se pueden leer: eso lo vuelve a comprobar el servidor en
/// cada petición. La UI oculta, el servidor prohíbe.
class Perfil {
  const Perfil({
    required this.usuarioId,
    required this.codigo,
    required this.nombre,
    required this.rol,
    required this.permisos,
  });

  factory Perfil.deJson(Map<String, Object?> json) => Perfil(
        usuarioId: json['usuario_id']! as String,
        codigo: json['codigo']! as String,
        nombre: json['nombre']! as String,
        rol: json['rol']! as String,
        permisos:
            ((json['permisos'] ?? const <Object?>[]) as List).cast<String>(),
      );

  final String usuarioId;
  final String codigo;
  final String nombre;
  final String rol;
  final List<String> permisos;

  bool puede(String permiso) => rol == 'admin' || permisos.contains(permiso);

  bool get puedeVerTablero => puede('tablero.ver');
}

/// La sesión en línea recién abierta.
class SesionEnLinea {
  const SesionEnLinea({
    required this.accessToken,
    required this.refreshToken,
    required this.expiraEnSeg,
    required this.perfil,
    this.credencialLocal,
  });

  factory SesionEnLinea.deJson(Map<String, Object?> json) => SesionEnLinea(
        accessToken: json['access_token']! as String,
        refreshToken: json['refresh_token']! as String,
        expiraEnSeg: json['expira_en_seg']! as int,
        perfil: json['perfil'] == null
            ? null
            : Perfil.deJson(json['perfil']! as Map<String, Object?>),
        credencialLocal: json['credencial_local'] == null
            ? null
            : CredencialLocal.deJson(
                json['credencial_local']! as Map<String, Object?>,
              ),
      );

  final String accessToken;
  final String refreshToken;
  final int expiraEnSeg;

  /// Nulo solo si el servidor es más viejo que esta app. La pantalla lo trata
  /// como "no sé quién entró" en vez de inventar un nombre.
  final Perfil? perfil;

  /// Solo viene cuando el login trae un dispositivo registrado: es lo que
  /// permite el login offline posterior. Un gerente no la recibe.
  final CredencialLocal? credencialLocal;
}

/// El servidor dijo que el código o la contraseña no sirven (401).
///
/// El mensaje del servidor es el mismo para "no existe ese usuario" y para
/// "contraseña incorrecta", a propósito: no se regala la existencia de una
/// cuenta. La app repite esa indistinción en vez de adivinar.
class CredencialesInvalidas implements Exception {
  const CredencialesInvalidas();

  @override
  String toString() => 'CredencialesInvalidas';
}

/// El servidor rechazó el login por una razón que se puede explicar.
///
/// El caso que importa: un vendedor intentando entrar por aquí. El servidor
/// contesta 400 con "un vendedor debe iniciar sesión desde un dispositivo
/// registrado", y esa frase es exactamente lo que hay que mostrar — mandarlo a
/// la pantalla de PIN sin explicación lo dejaría intentando lo mismo.
class LoginRechazado implements Exception {
  const LoginRechazado(this.codigo, this.detalle);

  final int codigo;
  final String detalle;

  @override
  String toString() => 'LoginRechazado ($codigo): $detalle';
}

class ClienteAuth {
  const ClienteAuth(this._transporte);

  final Transporte _transporte;

  Future<SesionEnLinea> entrar({
    required String codigo,
    required String password,
    String? dispositivoId,
  }) async {
    final respuesta = await _transporte.post('/v1/auth/login', {
      'codigo': codigo,
      'password': password,
      if (dispositivoId != null) 'dispositivo_id': dispositivoId,
    });
    _revisar(respuesta);
    return SesionEnLinea.deJson(
      jsonDecode(respuesta.cuerpo) as Map<String, Object?>,
    );
  }

  /// Renueva el access token con el refresh guardado.
  ///
  /// El refresh token NO cambia: el servidor devuelve el mismo. Así que la app
  /// no tiene que reescribir el Keystore en cada renovación.
  Future<SesionEnLinea> renovar(String refreshToken) async {
    final respuesta = await _transporte.post('/v1/auth/refresh', {
      'refresh_token': refreshToken,
    });
    _revisar(respuesta);
    return SesionEnLinea.deJson(
      jsonDecode(respuesta.cuerpo) as Map<String, Object?>,
    );
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw const CredencialesInvalidas();
    if (r.codigo >= 400 && r.codigo < 500) {
      throw LoginRechazado(r.codigo, _detalle(r.cuerpo));
    }
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }

  /// Saca el `detail` de FastAPI. Si el cuerpo no es el esperado, se devuelve
  /// crudo: un mensaje raro es más útil que "error desconocido".
  String _detalle(String cuerpo) {
    try {
      final json = jsonDecode(cuerpo);
      if (json is Map && json['detail'] is String) return json['detail'] as String;
    } on FormatException {
      // Cae al crudo.
    }
    return cuerpo;
  }
}
