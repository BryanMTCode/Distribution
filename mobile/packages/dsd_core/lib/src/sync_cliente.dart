/// Llamadas de sincronización al servidor.
///
/// Solo traduce entre los sobres del dispositivo y el JSON del endpoint. La
/// decisión de qué hacer con cada resultado vive en `sincronizador.dart`.
library;

import 'dart:convert';

import 'transporte.dart';

/// Lo que el servidor dijo de un sobre.
class ResultadoDeSobre {
  const ResultadoDeSobre({
    required this.operacionId,
    required this.estado,
    this.errorCodigo,
    this.errorMensaje,
  });

  final String operacionId;

  /// 'aceptada' | 'duplicada' | 'rechazada'
  final String estado;
  final String? errorCodigo;
  final String? errorMensaje;

  /// Aceptada y duplicada son lo mismo para el dispositivo: el sobre ya está
  /// del otro lado y puede salir de la cola.
  bool get quedoDelOtroLado => estado == 'aceptada' || estado == 'duplicada';
}

class RespuestaPush {
  const RespuestaPush({
    required this.aceptadas,
    required this.duplicadas,
    required this.rechazadas,
    required this.resultados,
  });

  factory RespuestaPush.deJson(Map<String, Object?> json) => RespuestaPush(
        aceptadas: json['aceptadas']! as int,
        duplicadas: json['duplicadas']! as int,
        rechazadas: json['rechazadas']! as int,
        resultados: ((json['resultados'] ?? const <Object?>[]) as List)
            .cast<Map<String, Object?>>()
            .map(
              (r) => ResultadoDeSobre(
                operacionId: r['operacion_id']! as String,
                estado: r['estado']! as String,
                errorCodigo: r['error_codigo'] as String?,
                errorMensaje: r['error_mensaje'] as String?,
              ),
            )
            .toList(),
      );

  final int aceptadas;
  final int duplicadas;
  final int rechazadas;
  final List<ResultadoDeSobre> resultados;
}

class Delta {
  const Delta({
    required this.cursor,
    required this.entidad,
    required this.entidadId,
    required this.operacion,
    this.payload,
  });

  final int cursor;
  final String entidad;
  final String entidadId;

  /// 'upsert' | 'delete'
  final String operacion;
  final Map<String, Object?>? payload;
}

class RespuestaPull {
  const RespuestaPull({
    required this.cursor,
    required this.hayMas,
    required this.cambios,
  });

  factory RespuestaPull.deJson(Map<String, Object?> json) => RespuestaPull(
        cursor: json['cursor']! as int,
        hayMas: json['hay_mas']! as bool,
        cambios: ((json['cambios'] ?? const <Object?>[]) as List)
            .cast<Map<String, Object?>>()
            .map(
              (c) => Delta(
                cursor: c['cursor']! as int,
                entidad: c['entidad']! as String,
                entidadId: c['entidad_id']! as String,
                operacion: c['operacion']! as String,
                payload: c['payload'] as Map<String, Object?>?,
              ),
            )
            .toList(),
      );

  final int cursor;
  final bool hayMas;
  final List<Delta> cambios;
}

/// El servidor rechazó el lote completo por estructura (HTTP 422).
///
/// Reenviar lo mismo va a fallar igual, así que reintentar sería un bucle
/// infinito que atora la cola. El sincronizador manda el lote a cuarentena.
class LoteRechazado implements Exception {
  const LoteRechazado(this.detalle);

  final String detalle;

  @override
  String toString() => 'LoteRechazado: $detalle';
}

/// El token ya no sirve. No se toca la cola: hay que volver a entrar.
class SesionInvalida implements Exception {
  const SesionInvalida(this.codigo);

  final int codigo;

  @override
  String toString() => 'SesionInvalida (HTTP $codigo)';
}

/// Falla del servidor, no del payload. Se reintenta más tarde.
class ServidorConProblemas implements Exception {
  const ServidorConProblemas(this.codigo, this.cuerpo);

  final int codigo;
  final String cuerpo;

  @override
  String toString() => 'ServidorConProblemas (HTTP $codigo)';
}

class ClienteSync {
  const ClienteSync(this._transporte);

  final Transporte _transporte;

  Future<RespuestaPush> push({
    required String loteId,
    required List<Map<String, Object?>> sobres,
    String? appVersion,
  }) async {
    final respuesta = await _transporte.post('/v1/sync/push', {
      'lote_id': loteId,
      'sobres': sobres,
      if (appVersion != null) 'app_version': appVersion,
    });
    _revisar(respuesta);
    return RespuestaPush.deJson(
      jsonDecode(respuesta.cuerpo) as Map<String, Object?>,
    );
  }

  Future<RespuestaPull> pull({required int cursor, int limite = 500}) async {
    final respuesta = await _transporte.obtener(
      '/v1/sync/pull',
      parametros: {'cursor': '$cursor', 'limite': '$limite'},
    );
    _revisar(respuesta);
    return RespuestaPull.deJson(
      jsonDecode(respuesta.cuerpo) as Map<String, Object?>,
    );
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401 || r.codigo == 403) throw SesionInvalida(r.codigo);
    if (r.codigo == 422) throw LoteRechazado(r.cuerpo);
    // 429 y 5xx: del servidor, no del contenido. Se reintenta.
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }
}
