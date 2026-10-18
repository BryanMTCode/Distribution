/// Cómo llegar a un cliente sin mapa ni señal (ADR 0002 §129).
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ SIN MAPA
/// ─────────────────────────────────────────────────────────────────────────
/// «El vendedor operará en zonas sin señal. No podemos depender de APIs de
/// mapas dentro de la app que consuman datos, se queden en blanco sin
/// internet, o inflen el APK con SDKs pesados.»
///
/// El GPS no usa datos: el satélite responde igual en una colonia sin
/// cobertura. Con la posición del teléfono y la coordenada de la tienda —que
/// ya está en la base local— alcanza para decir **a cuánto está y hacia dónde**.
/// Es lo que este archivo calcula, sin dibujar una sola calle.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA FLECHA: CONTRA EL NORTE, O CONTRA HACIA DONDE VA
/// ─────────────────────────────────────────────────────────────────────────
/// Para que una flecha diga «a tu derecha» hay que saber hacia dónde mira el
/// vendedor. Sin brújula (otro sensor, otro paquete), lo que da el GPS es el
/// **rumbo de marcha**: hacia dónde se movió entre una lectura y otra. Es
/// confiable cuando el teléfono se mueve —el camión en la calle, el vendedor
/// caminando— y basura cuando está quieto. Así que:
///
///   · en marcha, la flecha es relativa a la marcha: «de frente», «a tu
///     derecha»…;
///   · quieto, la flecha apunta contra el norte, con el rumbo en palabras
///     («al noreste»), y se le dice que camine unos pasos para orientarla.
library;

import 'ubicacion.dart';

/// Por debajo de esta velocidad el rumbo de marcha del GPS no sirve: parado, el
/// error de cada lectura mueve el «rumbo» para cualquier lado.
const velocidadMinimaParaOrientar = 0.8; // m/s, un paso tranquilo

/// A esta distancia, o dentro del margen de error del GPS, se le dice que llegó.
const metrosParaLlegar = 30.0;

/// Los ocho rumbos, en palabras: 0 = norte, en el sentido de las manecillas.
const _rumbos = [
  'norte', 'noreste', 'este', 'sureste', 'sur', 'suroeste', 'oeste', 'noroeste',
];

/// «al noreste»: el rumbo en grados (0..360, 0 = norte) a la palabra.
String rumboEnPalabras(double grados) {
  final normal = _normal(grados);
  return _rumbos[((normal + 22.5) ~/ 45) % 8];
}

/// «350 m», «1.2 km», «12 km»: como se dice en la calle.
String distanciaEnPalabras(double metros) {
  if (metros < 1000) {
    // De diez en diez: «a 347 m» es una precisión que el GPS no tiene.
    final redondeado = metros < 100 ? metros.round() : (metros / 10).round() * 10;
    return '$redondeado m';
  }
  final km = metros / 1000;
  return km < 10 ? '${km.toStringAsFixed(1)} km' : '${km.round()} km';
}

/// Hacia dónde está la tienda respecto a hacia dónde va el vendedor.
enum Indicacion {
  deFrente('de frente'),
  ligeramenteDerecha('un poco a la derecha'),
  derecha('a tu derecha'),
  ligeramenteIzquierda('un poco a la izquierda'),
  izquierda('a tu izquierda'),
  atras('atrás de ti: da la vuelta');

  const Indicacion(this.texto);

  final String texto;
}

/// Lo que la pantalla de la guía muestra, ya calculado.
class Guia {
  const Guia({
    required this.distanciaMetros,
    required this.rumboGrados,
    required this.anguloDeLaFlecha,
    required this.orientadaPorMarcha,
    required this.llegaste,
    this.indicacion,
  });

  /// Calcula la guía con la posición del teléfono y la de la tienda.
  ///
  /// `rumboDeMarcha` y `velocidad` son los del GPS (grados y m/s); si faltan, o
  /// el teléfono va más despacio que [velocidadMinimaParaOrientar], la flecha
  /// queda contra el norte.
  factory Guia.calcular({
    required double desdeLat,
    required double desdeLng,
    required double hastaLat,
    required double hastaLng,
    double? rumboDeMarcha,
    double? velocidad,
    double? precisionMetros,
  }) {
    // Las cuentas de `Ubicacion` (semiverseno y rumbo inicial), las mismas del
    // lienzo espacial y de la geocerca.
    final desde = Ubicacion(lat: desdeLat, lng: desdeLng, origen: OrigenUbicacion.gps);
    final hasta = Ubicacion(lat: hastaLat, lng: hastaLng, origen: OrigenUbicacion.gps);
    final distancia = desde.distanciaA(hasta);
    final rumbo = desde.rumboA(hasta);
    final enMarcha = rumboDeMarcha != null &&
        rumboDeMarcha >= 0 &&
        velocidad != null &&
        velocidad >= velocidadMinimaParaOrientar;
    // Lo que se gira la flecha: contra la marcha, o contra el norte.
    final angulo = enMarcha ? _relativo(rumbo - rumboDeMarcha) : rumbo;
    final margen = precisionMetros == null
        ? metrosParaLlegar
        : (precisionMetros > metrosParaLlegar ? precisionMetros : metrosParaLlegar);
    return Guia(
      distanciaMetros: distancia,
      rumboGrados: rumbo,
      anguloDeLaFlecha: angulo,
      orientadaPorMarcha: enMarcha,
      llegaste: distancia <= margen,
      indicacion: enMarcha ? indicacionPara(angulo) : null,
    );
  }

  final double distanciaMetros;

  /// Hacia dónde está la tienda, en grados desde el norte (0..360).
  final double rumboGrados;

  /// Cuánto se gira la flecha, en grados en el sentido de las manecillas: desde
  /// el frente de la marcha si [orientadaPorMarcha], si no desde el norte.
  /// En marcha va de -180 a 180.
  final double anguloDeLaFlecha;

  final bool orientadaPorMarcha;

  /// Ya está ahí (o tan cerca como el GPS sabe decir).
  final bool llegaste;

  /// En palabras, solo en marcha.
  final Indicacion? indicacion;

  String get distanciaTexto => distanciaEnPalabras(distanciaMetros);
  String get rumboTexto => rumboEnPalabras(rumboGrados);
}

/// El ángulo relativo a la marcha (-180..180) a la indicación.
Indicacion indicacionPara(double anguloRelativo) {
  final a = _relativo(anguloRelativo);
  final abs = a.abs();
  if (abs <= 20) return Indicacion.deFrente;
  if (abs <= 60) return a > 0 ? Indicacion.ligeramenteDerecha : Indicacion.ligeramenteIzquierda;
  if (abs <= 135) return a > 0 ? Indicacion.derecha : Indicacion.izquierda;
  return Indicacion.atras;
}

double _normal(double grados) => (grados % 360 + 360) % 360;

/// A -180..180: «200° a la derecha» es «160° a la izquierda».
double _relativo(double grados) {
  final n = _normal(grados);
  return n > 180 ? n - 360 : n;
}
