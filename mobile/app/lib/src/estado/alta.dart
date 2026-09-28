/// Estado del alta de cliente.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../datos/servicio_ubicacion.dart';
import 'sesion.dart';

/// Se sobrescribe en el arranque y en las pruebas.
final servicioUbicacionProvider = Provider<ServicioUbicacion>(
  (_) => const ServicioUbicacionDelSistema(),
);

/// Generador de identificadores. Inyectable para que las pruebas sean
/// reproducibles.
///
/// UUIDv7: ordenable por tiempo, que es lo que hace que la llave primaria no
/// fragmente el índice ni en el teléfono ni en PostgreSQL.
final uuidProvider = Provider<String Function()>((_) => () => const Uuid().v7());

final altasProvider = Provider<AltaDeClientes>((ref) {
  final base = ref.watch(baseLocalProvider);
  // El reloj se resuelve AQUÍ, no dentro del callback: un `ref.watch` que se
  // ejecuta después de construir el provider lanza en Riverpod, y el fallo
  // aparecería justo al guardar el cliente — es decir, en la calle.
  final reloj = ref.watch(relojProvider);
  return AltaDeClientes(
    db: base.db,
    outbox: ref.watch(outboxProvider),
    nuevoUuid: ref.watch(uuidProvider),
    ahora: () => reloj().toUtc().toIso8601String(),
  );
});

/// Lo que la pantalla sabe de la ubicación en cada momento.
class EstadoUbicacion {
  const EstadoUbicacion({
    this.leyendo = false,
    this.lectura,
    this.ubicacion,
    this.metrosMovidos = 0,
  });

  final bool leyendo;

  /// Resultado del último intento. Nulo antes del primero.
  final LecturaGps? lectura;

  /// La coordenada vigente, ya con las correcciones manuales aplicadas.
  final Ubicacion? ubicacion;

  /// Cuánto se movió respecto de la lectura original. Se muestra para que el
  /// vendedor sepa qué tanto corrigió.
  final double metrosMovidos;

  EstadoUbicacion copiarCon({
    bool? leyendo,
    LecturaGps? lectura,
    Ubicacion? ubicacion,
    double? metrosMovidos,
  }) =>
      EstadoUbicacion(
        leyendo: leyendo ?? this.leyendo,
        lectura: lectura ?? this.lectura,
        ubicacion: ubicacion ?? this.ubicacion,
        metrosMovidos: metrosMovidos ?? this.metrosMovidos,
      );
}

class ControladorUbicacion extends Notifier<EstadoUbicacion> {
  Ubicacion? _original;

  @override
  EstadoUbicacion build() => const EstadoUbicacion();

  Future<void> leer() async {
    state = state.copiarCon(leyendo: true);
    final lectura = await ref.read(servicioUbicacionProvider).leer();

    if (lectura is GpsObtenido) {
      _original = lectura.ubicacion;
      state = EstadoUbicacion(lectura: lectura, ubicacion: lectura.ubicacion);
    } else {
      // Sin lectura no se borra lo que ya había: si el vendedor ya corrigió un
      // punto a mano y vuelve a intentar el GPS sin éxito, perder su corrección
      // sería peor que no intentar.
      state = state.copiarCon(leyendo: false, lectura: lectura);
    }
  }

  /// Corrección manual, sin mapa.
  ///
  /// Un mapa con mosaicos necesita red, y la corrección se hace justo donde no
  /// la hay. Con desplazamientos cardinales el vendedor acerca el punto a la
  /// puerta del negocio dentro de un mercado techado, sin descargar nada.
  void mover({double norte = 0, double este = 0}) {
    final actual = state.ubicacion;
    if (actual == null) return;
    final movida = actual.desplazada(norte: norte, este: este);
    state = state.copiarCon(
      ubicacion: movida,
      metrosMovidos: _original == null ? 0 : movida.distanciaA(_original!),
    );
  }

  /// Vuelve al punto que dio el satélite.
  void deshacerCorreccion() {
    if (_original == null) return;
    state = state.copiarCon(ubicacion: _original, metrosMovidos: 0);
  }
}

final ubicacionProvider = NotifierProvider<ControladorUbicacion, EstadoUbicacion>(
  ControladorUbicacion.new,
);

/// Clientes ya conocidos cerca del punto actual.
final cercanosProvider = Provider<List<PosibleDuplicado>>((ref) {
  final ubicacion = ref.watch(ubicacionProvider).ubicacion;
  return ref.watch(altasProvider).cercanos(ubicacion);
});
