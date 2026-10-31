/// Abrir una dirección en el navegador del teléfono (ADR 0002 §194).
///
/// Para el Excel del dashboard —el navegador sabe bajarlo y abrirlo— y para un
/// enlace a otro sitio. Como el mapa (`abridor_de_mapas.dart`): un Intent de
/// Android y nada más, sin un paquete más dentro del APK.
library;

import 'package:flutter/services.dart';

abstract interface class AbridorDeNavegador {
  /// `false` si no se pudo: no hay navegador, o la dirección no es web.
  Future<bool> abrir(Uri url);
}

class AbridorDeNavegadorDelSistema implements AbridorDeNavegador {
  const AbridorDeNavegadorDelSistema();

  static const _canal = MethodChannel('dsd/navegador');

  @override
  Future<bool> abrir(Uri url) async {
    if (url.scheme != 'https' && url.scheme != 'http') return false;
    try {
      return await _canal.invokeMethod<bool>('abrir', {'url': url.toString()}) ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      // En una prueba o un escritorio no hay `MainActivity`.
      return false;
    }
  }
}
