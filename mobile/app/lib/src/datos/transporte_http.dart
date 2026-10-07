/// Transporte HTTP real.
///
/// Traduce los fallos de red a `ErrorDeRed`, que es lo que el sincronizador
/// distingue de un rechazo del servidor: cuando la petición no vuelve, **no se
/// sabe** si se aplicó, y el sobre tiene que conservarse.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:http/http.dart' as http;

class TransporteHttp implements Transporte {
  TransporteHttp({
    required this.baseUrl,
    required this.token,
    this.leerToken,
    http.Client? cliente,
    this.tiempoLimite = const Duration(seconds: 30),
  }) : _cliente = cliente ?? http.Client();

  final String baseUrl;

  /// Access token. Cuando caduca, el servidor responde 401 y el sincronizador
  /// se detiene sin tocar la cola.
  final String token;

  /// Si se da, manda sobre `token`: se lee en CADA petición. Es lo que permite
  /// que `TransporteRenovable` repita una petición con el token recién renovado
  /// sin armar otro transporte.
  final String Function()? leerToken;
  final Duration tiempoLimite;
  final http.Client _cliente;

  Map<String, String> get _cabeceras {
    final token = leerToken?.call() ?? this.token;
    return {
        // Sin token no se manda la cabecera. Un `Authorization: Bearer ` vacío
        // no significa "sin credencial": es una credencial mal formada, y el
        // servidor la contesta con 401 antes de llegar al endpoint. Eso
        // taparía el 400 explicativo del login —"un vendedor debe iniciar
        // sesión desde un dispositivo registrado"— que es el único mensaje
        // útil que hay en ese caso.
        if (token.isNotEmpty) 'Authorization': 'Bearer $token',
        'Content-Type': 'application/json; charset=utf-8',
      };
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) =>
      _intentar(() async {
        final r = await _cliente
            .post(
              Uri.parse('$baseUrl$ruta'),
              headers: _cabeceras,
              // El cuerpo se codifica aquí y no con jsonEncode del llamador
              // para garantizar UTF-8: un nombre con ñ o emoji cambiaría el
              // hash si se enviara en otra codificación.
              body: _codificar(cuerpo),
            )
            .timeout(tiempoLimite);
        return RespuestaHttp(r.statusCode, utf8.decode(r.bodyBytes));
      });

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) =>
      _intentar(() async {
        final uri = Uri.parse('$baseUrl$ruta').replace(queryParameters: parametros);
        final r = await _cliente.get(uri, headers: _cabeceras).timeout(tiempoLimite);
        return RespuestaHttp(r.statusCode, utf8.decode(r.bodyBytes));
      });

  Future<RespuestaHttp> _intentar(Future<RespuestaHttp> Function() peticion) async {
    try {
      return await peticion();
    } on SocketException catch (e) {
      throw ErrorDeRed('sin conexión: ${e.message}');
    } on TimeoutException {
      // Tiempo agotado: puede que el servidor SÍ lo haya aplicado. Se trata
      // como error de red, nunca como rechazo.
      throw const ErrorDeRed('el servidor no respondió a tiempo');
    } on http.ClientException catch (e) {
      throw ErrorDeRed('falló la petición: ${e.message}');
    } on HandshakeException catch (e) {
      throw ErrorDeRed('TLS: ${e.message}');
    }
  }

  String _codificar(Map<String, Object?> cuerpo) => jsonEncode(cuerpo);

  void cerrar() => _cliente.close();
}
