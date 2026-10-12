/// Lo que el servidor contesta a una pantalla de la oficina, en una excepción
/// que la pantalla puede explicar. Lo comparten los objetivos y el mayoreo.
library;

import 'dart:convert';

import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';
import 'vendedores_cliente.dart' show ServidorSinEstaFuncion;

/// El servidor dijo que no a algo que la persona puede corregir: trae su texto,
/// que es el que dice qué hacer.
class RechazoDeLaOficina implements Exception {
  const RechazoDeLaOficina(this.codigo, this.detalle);

  final int codigo;
  final String detalle;

  @override
  String toString() => detalle;
}

/// Falta el permiso (403).
class SinPermisoDeLaOficina implements Exception {
  const SinPermisoDeLaOficina();
}

/// Revisa una respuesta y la convierte en la excepción que toca.
void revisarRespuestaDeOficina(RespuestaHttp r) {
  if (r.ok) return;
  if (r.codigo == 401) throw SesionInvalida(r.codigo);
  if (r.codigo == 403) throw const SinPermisoDeLaOficina();
  // «Not Found» a secas es que la RUTA no existe: el servidor no se ha
  // actualizado.
  if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
    throw const ServidorSinEstaFuncion();
  }
  if (r.codigo >= 400 && r.codigo < 500) {
    throw RechazoDeLaOficina(r.codigo, _detalle(r.cuerpo));
  }
  throw ServidorConProblemas(r.codigo, r.cuerpo);
}

String _detalle(String cuerpo) {
  try {
    final json = jsonDecode(cuerpo);
    if (json is Map && json['detail'] is String) return json['detail'] as String;
  } on FormatException {
    // Cae al crudo.
  }
  return cuerpo;
}
