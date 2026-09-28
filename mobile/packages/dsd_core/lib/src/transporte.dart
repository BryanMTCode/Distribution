/// Transporte HTTP, abstraído.
///
/// Se abstrae para que el sincronizador se pueda probar contra un servidor
/// falso: la lógica interesante —qué hacer con cada tipo de fallo— es
/// justamente la que no se puede ejercer con un servidor real de manera
/// confiable. "Que se caiga la red a media tanda" no es algo que se pida a
/// voluntad.
library;

/// Respuesta que SÍ llegó del servidor, con cualquier código.
class RespuestaHttp {
  const RespuestaHttp(this.codigo, this.cuerpo);

  final int codigo;
  final String cuerpo;

  bool get ok => codigo >= 200 && codigo < 300;
}

/// La petición no llegó, o no volvió: sin señal, DNS, timeout, socket cortado.
///
/// Distinto de un error HTTP: aquí **no se sabe** si el servidor procesó algo.
/// Por eso el sobre se queda pendiente y se reintenta — y por eso el servidor
/// tiene que ser idempotente.
class ErrorDeRed implements Exception {
  const ErrorDeRed(this.mensaje);

  final String mensaje;

  @override
  String toString() => 'ErrorDeRed: $mensaje';
}

abstract interface class Transporte {
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo);

  Future<RespuestaHttp> obtener(String ruta, {Map<String, String> parametros});
}
