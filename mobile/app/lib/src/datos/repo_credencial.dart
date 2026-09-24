/// Guarda y lee la credencial del login offline.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';

import 'almacen_seguro.dart';

const _clave = 'credencial_local_v1';

class RepoCredencial {
  const RepoCredencial(this._almacen);

  final AlmacenSeguro _almacen;

  /// Se llama con lo que devuelve `POST /v1/auth/login`.
  Future<void> guardar(Map<String, Object?> credencialDelServidor) =>
      _almacen.escribir(_clave, jsonEncode(credencialDelServidor));

  Future<CredencialLocal?> leer() async {
    final crudo = await _almacen.leer(_clave);
    if (crudo == null) return null;
    try {
      return CredencialLocal.deJson(jsonDecode(crudo) as Map<String, Object?>);
    } on Object {
      // Una credencial ilegible es como no tenerla: se pide login online en
      // vez de dejar la app en un estado a medias.
      return null;
    }
  }

  /// Cierre de sesión o revocación del equipo.
  Future<void> olvidar() => _almacen.borrar(_clave);
}
