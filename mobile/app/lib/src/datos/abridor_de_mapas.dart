/// Abrir la ubicación de un cliente en la app de mapas del teléfono (ADR 0002 §129).
///
/// Sin SDK de mapas dentro del APK: se le pasa la coordenada a lo que el
/// vendedor ya tiene —Google Maps, Waze, Organic Maps— con un `Intent` de Android
/// (`geo:`), y esa app hace la ruta por calles. Funciona sin señal si esa app
/// tiene descargado el mapa de la zona; eso se hace una vez, con WiFi.
///
/// El puente son unas líneas en `MainActivity.kt`, no un paquete: no hay nada
/// más que bajar ni que pese.
library;

import 'package:flutter/services.dart';

abstract interface class AbridorDeMapas {
  /// `false` si el teléfono no tiene ninguna app que abra mapas.
  Future<bool> abrir({required double lat, required double lng, required String nombre});
}

class AbridorDeMapasDelSistema implements AbridorDeMapas {
  const AbridorDeMapasDelSistema();

  static const _canal = MethodChannel('dsd/mapas');

  @override
  Future<bool> abrir({required double lat, required double lng, required String nombre}) async {
    try {
      final abrio = await _canal.invokeMethod<bool>('abrir', {
        // Siete decimales, como se guardan: ~1 cm.
        'lat': lat.toStringAsFixed(7),
        'lng': lng.toStringAsFixed(7),
        'nombre': nombre,
      });
      return abrio ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      // En una prueba o un escritorio no hay `MainActivity`.
      return false;
    }
  }
}

/// Para pruebas: dice que abrió y recuerda qué.
class AbridorDeMapasFalso implements AbridorDeMapas {
  AbridorDeMapasFalso({this.hayApp = true});

  final bool hayApp;
  final List<(double, double, String)> abiertos = [];

  @override
  Future<bool> abrir({required double lat, required double lng, required String nombre}) async {
    abiertos.add((lat, lng, nombre));
    return hayApp;
  }
}
