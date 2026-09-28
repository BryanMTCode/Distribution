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
  const GpsObtenido(this.ubicacion);

  final Ubicacion ubicacion;
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
}
