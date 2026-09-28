/// Georreferencia de un punto de venta.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL GPS NUNCA BLOQUEA UN ALTA
/// ─────────────────────────────────────────────────────────────────────────
/// La tienda existe aunque el satélite no colabore. Dentro de un mercado
/// techado, en un sótano de bodega o entre dos edificios, el GPS entrega basura
/// o nada. Si el alta exigiera una lectura buena, el vendedor no podría
/// registrar al cliente que tiene enfrente — y lo que haría es no registrarlo.
///
/// Por eso la precisión se **muestra y se guarda**, pero no se impone. El
/// campo `origen` deja constancia de si la coordenada la puso el satélite o la
/// corrigió el vendedor: es dato auditable, no una anomalía.
library;

import 'dart:math' as math;

/// De dónde salió la coordenada.
enum OrigenUbicacion {
  /// Lectura del satélite.
  gps('gps'),

  /// El vendedor la corrigió porque el GPS estaba mal o no había señal.
  manual('manual');

  const OrigenUbicacion(this.codigo);

  final String codigo;
}

/// Qué tan confiable es una lectura.
///
/// Los umbrales salen de cómo se usa en la calle: con menos de 20 m la
/// coordenada cae en la cuadra correcta, con menos de 50 m sirve para encontrar
/// el negocio, y arriba de eso puede apuntar a otra calle.
enum CalidadGps {
  buena,
  aceptable,
  mala,
  sinLectura;

  static CalidadGps de(double? precisionMetros) => switch (precisionMetros) {
        null => CalidadGps.sinLectura,
        final p when p <= 20 => CalidadGps.buena,
        final p when p <= 50 => CalidadGps.aceptable,
        _ => CalidadGps.mala,
      };

  /// Si conviene sugerirle al vendedor que ajuste a mano. Es una sugerencia:
  /// puede guardar igual.
  bool get convieneAjustar =>
      this == CalidadGps.mala || this == CalidadGps.sinLectura;
}

class UbicacionInvalida implements Exception {
  const UbicacionInvalida(this.mensaje);

  final String mensaje;

  @override
  String toString() => 'UbicacionInvalida: $mensaje';
}

class Ubicacion {
  Ubicacion({
    required this.lat,
    required this.lng,
    required this.origen,
    this.precisionMetros,
  }) {
    if (lat < -90 || lat > 90) {
      throw UbicacionInvalida('latitud fuera de rango: $lat');
    }
    if (lng < -180 || lng > 180) {
      throw UbicacionInvalida('longitud fuera de rango: $lng');
    }
    if (precisionMetros != null && precisionMetros! < 0) {
      throw UbicacionInvalida('precisión negativa: $precisionMetros');
    }
  }

  final double lat;
  final double lng;
  final OrigenUbicacion origen;

  /// Radio de error que reporta el dispositivo, en metros.
  final double? precisionMetros;

  CalidadGps get calidad => CalidadGps.de(precisionMetros);

  /// Los siete decimales del contrato: ~1 cm de resolución, de sobra, y la
  /// misma escala que `numeric(10,7)` en PostgreSQL.
  String get latTexto => lat.toStringAsFixed(7);
  String get lngTexto => lng.toStringAsFixed(7);

  /// Como viaja en el payload del sobre. Los números van como string porque el
  /// hash canónico prohíbe flotantes (contracts/README.md §1).
  Map<String, Object?> aPayload() => {
        'lat': latTexto,
        'lng': lngTexto,
        'ubicacion_origen': origen.codigo,
        if (precisionMetros != null)
          'ubicacion_precision_m': precisionMetros!.toStringAsFixed(2),
      };

  /// Mueve el punto los metros indicados. Es la corrección manual sin mapa.
  ///
  /// Un mapa con mosaicos necesita red, y la corrección se hace justo donde no
  /// la hay. Con desplazamientos cardinales el vendedor acerca el punto a la
  /// puerta del negocio dentro de un mercado techado, sin descargar nada.
  Ubicacion desplazada({double norte = 0, double este = 0}) {
    // Un grado de latitud ≈ 111,320 m en cualquier parte. Para la longitud hay
    // que corregir por el coseno de la latitud: en Ciudad de México un grado de
    // longitud son ~105 km, no 111.
    const metrosPorGradoLat = 111320.0;
    final metrosPorGradoLng = metrosPorGradoLat * math.cos(lat * math.pi / 180);

    return Ubicacion(
      lat: lat + norte / metrosPorGradoLat,
      lng: metrosPorGradoLng.abs() < 1e-9
          ? lng // en los polos la longitud deja de tener sentido métrico
          : lng + este / metrosPorGradoLng,
      // Corregida a mano: deja de ser una lectura del satélite, y la precisión
      // que reportaba el GPS ya no describe este punto.
      origen: OrigenUbicacion.manual,
      precisionMetros: null,
    );
  }

  /// Distancia en metros por la fórmula del semiverseno.
  ///
  /// Se usa para dos cosas concretas: avisar de un posible duplicado antes de
  /// crearlo, y medir qué tan lejos quedó una venta del domicilio registrado.
  double distanciaA(Ubicacion otra) {
    const radioTierraM = 6371000.0;
    double aRadianes(double g) => g * math.pi / 180;

    final dLat = aRadianes(otra.lat - lat);
    final dLng = aRadianes(otra.lng - lng);
    final a = math.pow(math.sin(dLat / 2), 2) +
        math.cos(aRadianes(lat)) *
            math.cos(aRadianes(otra.lat)) *
            math.pow(math.sin(dLng / 2), 2);
    return radioTierraM * 2 * math.asin(math.min(1, math.sqrt(a)));
  }

  @override
  String toString() =>
      'Ubicacion($latTexto, $lngTexto, ${origen.codigo}'
      '${precisionMetros == null ? '' : ', ±${precisionMetros!.round()} m'})';
}
