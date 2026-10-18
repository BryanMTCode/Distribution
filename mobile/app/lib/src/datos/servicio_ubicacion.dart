/// Lectura del GPS.
///
/// Se abstrae porque los casos que importan son los que fallan: permiso negado,
/// GPS apagado, y sobre todo la lectura que tarda y nunca llega dentro de un
/// mercado techado. Ninguno de esos se puede provocar a voluntad en un
/// dispositivo real, y todos tienen que estar probados.
library;

import 'dart:async';

import 'package:dsd_core/dsd_core.dart';
import 'package:geolocator/geolocator.dart';

/// Resultado de pedir la ubicación. Cada caso pide algo distinto del vendedor.
sealed class LecturaGps {
  const LecturaGps();
}

class GpsObtenido extends LecturaGps {
  const GpsObtenido(this.ubicacion, {this.rumboDeMarcha, this.velocidad});

  final Ubicacion ubicacion;

  /// Hacia dónde se mueve el teléfono, en grados desde el norte, y a cuántos m/s.
  /// Solo en `seguir`: es lo que orienta la flecha de la guía (ADR 0002 §129).
  final double? rumboDeMarcha;
  final double? velocidad;
}

/// El vendedor negó el permiso. Se puede volver a pedir.
class GpsSinPermiso extends LecturaGps {
  const GpsSinPermiso({this.definitivo = false});

  /// `true` cuando lo negó para siempre: hay que mandarlo a los ajustes del
  /// sistema, porque volver a pedirlo ya no muestra nada.
  final bool definitivo;
}

/// La ubicación del teléfono está apagada.
class GpsApagado extends LecturaGps {
  const GpsApagado();
}

/// No hubo lectura a tiempo. Es el caso más común en ruta: dentro de un mercado
/// techado el satélite no aparece nunca.
class GpsSinLectura extends LecturaGps {
  const GpsSinLectura([this.detalle]);

  final String? detalle;
}

abstract interface class ServicioUbicacion {
  Future<LecturaGps> leer({Duration tiempoLimite});

  /// Lecturas continuas mientras alguien escucha: la guía hacia un cliente
  /// (ADR 0002 §129). Es el mismo GPS, sin datos. El primer elemento puede ser
  /// un error (sin permiso, apagado), y entonces no viene nada más.
  Stream<LecturaGps> seguir();
}

class ServicioUbicacionDelSistema implements ServicioUbicacion {
  const ServicioUbicacionDelSistema();

  @override
  Future<LecturaGps> leer({Duration tiempoLimite = const Duration(seconds: 12)}) async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return const GpsApagado();
    }

    var permiso = await Geolocator.checkPermission();
    if (permiso == LocationPermission.denied) {
      permiso = await Geolocator.requestPermission();
    }
    if (permiso == LocationPermission.deniedForever) {
      return const GpsSinPermiso(definitivo: true);
    }
    if (permiso == LocationPermission.denied) {
      return const GpsSinPermiso();
    }

    try {
      final posicion = await Geolocator.getCurrentPosition(
        locationSettings: LocationSettings(
          accuracy: LocationAccuracy.high,
          timeLimit: tiempoLimite,
        ),
      );
      return GpsObtenido(
        Ubicacion(
          lat: posicion.latitude,
          lng: posicion.longitude,
          origen: OrigenUbicacion.gps,
          precisionMetros: posicion.accuracy,
        ),
      );
    } on TimeoutException {
      // No es un error que haya que explicarle como falla: es lo normal bajo
      // techo. El alta sigue adelante sin coordenadas.
      return const GpsSinLectura('el satélite no respondió a tiempo');
    } on UbicacionInvalida catch (e) {
      return GpsSinLectura(e.mensaje);
    } on Exception catch (e) {
      return GpsSinLectura(e.toString());
    }
  }

  @override
  Stream<LecturaGps> seguir() async* {
    if (!await Geolocator.isLocationServiceEnabled()) {
      yield const GpsApagado();
      return;
    }
    var permiso = await Geolocator.checkPermission();
    if (permiso == LocationPermission.denied) {
      permiso = await Geolocator.requestPermission();
    }
    if (permiso == LocationPermission.deniedForever) {
      yield const GpsSinPermiso(definitivo: true);
      return;
    }
    if (permiso == LocationPermission.denied) {
      yield const GpsSinPermiso();
      return;
    }
    // Cada 3 m o más: lo bastante seguido para que la flecha siga al camión en
    // una vuelta, sin despertar al GPS por cada centímetro de error.
    final lecturas = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 3,
      ),
    );
    try {
      await for (final p in lecturas) {
        try {
          yield GpsObtenido(
            Ubicacion(
              lat: p.latitude,
              lng: p.longitude,
              origen: OrigenUbicacion.gps,
              precisionMetros: p.accuracy,
            ),
            rumboDeMarcha: p.heading >= 0 ? p.heading : null,
            velocidad: p.speed >= 0 ? p.speed : null,
          );
        } on UbicacionInvalida catch (e) {
          yield GpsSinLectura(e.mensaje);
        }
      }
    } on LocationServiceDisabledException {
      // Apagó la ubicación a medio camino: se dice, no se truena.
      yield const GpsApagado();
    } on PermissionDeniedException {
      yield const GpsSinPermiso();
    } on Exception catch (e) {
      yield GpsSinLectura(e.toString());
    }
  }
}

/// Para pruebas. Devuelve lo que se le diga, sin tocar el hardware.
class ServicioUbicacionFalso implements ServicioUbicacion {
  ServicioUbicacionFalso(this._respuestas);

  factory ServicioUbicacionFalso.siempre(LecturaGps respuesta) =>
      ServicioUbicacionFalso([respuesta]);

  final List<LecturaGps> _respuestas;
  int _i = 0;
  int lecturas = 0;

  @override
  Future<LecturaGps> leer({Duration tiempoLimite = const Duration(seconds: 12)}) async {
    lecturas++;
    if (_respuestas.isEmpty) return const GpsSinLectura();
    final r = _respuestas[_i.clamp(0, _respuestas.length - 1)];
    _i++;
    return r;
  }

  /// Lo que la prueba empuje, en orden: `empujar` simula al camión moviéndose.
  final _seguimiento = StreamController<LecturaGps>.broadcast();
  int seguidores = 0;

  void empujar(LecturaGps lectura) => _seguimiento.add(lectura);

  @override
  Stream<LecturaGps> seguir() async* {
    seguidores++;
    for (final r in _respuestas) {
      yield r;
    }
    yield* _seguimiento.stream;
  }
}
