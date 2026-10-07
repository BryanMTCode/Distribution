/// Transporte que renueva el acceso solo, sin pedir la contraseña.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ EXISTE: «ME SACA A CADA RATO» (octubre 2026)
/// ─────────────────────────────────────────────────────────────────────────
/// El access token que da el servidor dura 30 minutos, y la app no lo renovaba
/// nunca. El refresh token —que dura 30 días— estaba guardado en el Keystore
/// desde el login, pero nadie lo usaba fuera del arranque de Gerencia. Así que:
///
///   · Gerencia abría el tablero a la media hora y leía «la sesión venció».
///   · El vendedor que entró a las 7 y sincronizó a las 8 se quedaba sin poder
///     subir, con «vuelve a entrar» y la contraseña otra vez.
///
/// Este transporte envuelve al real: si el servidor contesta 401, pide un token
/// nuevo con el refresh guardado y repite la petición UNA vez. Repetir es seguro
/// porque el servidor es idempotente: un push repetido se contesta «duplicada».
///
/// Si la renovación falla —refresh revocado, equipo dado de baja, sin señal— se
/// devuelve el 401 original y cada pantalla dice lo que ya decía.
library;

import 'package:dsd_core/dsd_core.dart';

class TransporteRenovable implements Transporte {
  TransporteRenovable(this._interno, {required Future<bool> Function() renovar})
      : _renovar = renovar;

  /// Lee el token vigente en cada petición: después de renovar, la repetición
  /// sale ya con el nuevo.
  final Transporte _interno;
  final Future<bool> Function() _renovar;

  /// Una sola renovación a la vez. La sincronización y el tablero pueden toparse
  /// con el 401 al mismo tiempo, y dos renovaciones en paralelo serían dos
  /// viajes para conseguir lo mismo.
  Future<bool>? _enCurso;

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) =>
      _conRenovacion(() => _interno.post(ruta, cuerpo));

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) =>
      _conRenovacion(
        () => parametros == null
            ? _interno.obtener(ruta)
            : _interno.obtener(ruta, parametros: parametros),
      );

  Future<RespuestaHttp> _conRenovacion(Future<RespuestaHttp> Function() peticion) async {
    final respuesta = await peticion();
    if (respuesta.codigo != 401) return respuesta;
    final renovado = await (_enCurso ??= _renovar().whenComplete(() => _enCurso = null));
    if (!renovado) return respuesta;
    return peticion();
  }
}
