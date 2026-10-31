/// El estado del no-drop: «No me compró».
///
/// La merma, la devolución y el cambio físico se quitaron de la app de los
/// vendedores (ADR 0002 §197): el vendedor lleva lo malo a la bodega, ahí se le
/// cambia por bueno, y la oficina lo registra en el dashboard. Lo que se quedó
/// aquí es lo que comparte el no-drop.
///
/// El catálogo de motivos (`motivos_no_drop`) llega por delta. Un equipo que
/// nunca sincronizó lo tiene vacío, y entonces **no hay nada que capturar**: la
/// pantalla lo dice con esas palabras en vez de mostrar un selector sin
/// opciones, porque un desplegable vacío parece una falla de la aplicación y no
/// un equipo sin sincronizar.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'carrito.dart';
import 'sesion.dart';

final registroDeNoDropProvider = Provider<RegistroDeNoDrop?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;

  final dispositivoId = ref.watch(dispositivoIdProvider);
  if (dispositivoId == null) return null;

  final reloj = ref.watch(relojProvider);
  return RegistroDeNoDrop(
    db: ref.watch(baseLocalProvider).db,
    vendedorId: sesion.credencial.usuarioId,
    dispositivoId: dispositivoId,
    outbox: ref.watch(outboxProvider),
    folios: ref.watch(repoFoliosProvider),
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: reloj,
    // Sin ruta a propósito: el servidor la toma del CLIENTE, que es la única
    // fuente que no puede estar desfasada. La credencial no la trae, y
    // adivinarla aquí solo abriría la puerta a que una visita quedara contada
    // en la ruta equivocada.
  );
});

/// El identificador que el servidor le dio a este equipo.
///
/// Está en `sync_estado`, no en la credencial: lo asigna el registro del
/// dispositivo, que es un paso distinto del login.
final dispositivoIdProvider = Provider<String?>((ref) {
  final filas = ref.watch(baseLocalProvider).db.select(
        "SELECT valor FROM sync_estado WHERE clave = 'dispositivo_id'",
      );
  if (filas.isEmpty) return null;
  return filas.single['valor'] as String?;
});

/// Los motivos de no-drop, en el orden que definió la oficina.
///
/// `orden` existe porque en la calle, con el cliente esperando, un catálogo
/// alfabético obliga a leer diez opciones para encontrar "cerrado".
final motivosDeNoDropProvider = Provider<List<MotivoDeNoDrop>>(
  (ref) => ref.watch(registroDeNoDropProvider)?.motivos() ?? const [],
);

final foliosDeNoDropProvider = Provider<RangoFolios?>(
  (ref) => ref.watch(repoFoliosProvider).leer('no_drop'),
);

// ---------------------------------------------------------------------------
// El no-drop
// ---------------------------------------------------------------------------

sealed class EstadoNoDrop {
  const EstadoNoDrop();
}

class NoDropInactivo extends EstadoNoDrop {
  const NoDropInactivo();
}

class NoDropEnCurso extends EstadoNoDrop {
  const NoDropEnCurso();
}

class NoDropRegistrado extends EstadoNoDrop {
  const NoDropRegistrado(this.noDrop);

  final NoDropGuardado noDrop;
}

class NoDropFallido extends EstadoNoDrop {
  const NoDropFallido(this.motivo, [this.detalle]);

  final MotivoNoNoDrop motivo;
  final String? detalle;

  String get mensaje => switch (motivo) {
        MotivoNoNoDrop.motivoDesconocido =>
          'Ese motivo ya no está en el catálogo. Sincroniza y escoge otro.',
        MotivoNoNoDrop.faltaNota =>
          'Este motivo necesita que escribas qué pasó: sin explicación el dato '
              'no sirve para nada.',
        // Ya no sale: desde la +52 la visita sin GPS se registra igual (ADR
        // 0002 §158).
        MotivoNoNoDrop.faltaUbicacion =>
          'Falta la ubicación. Vuelve a leer el GPS.',
        MotivoNoNoDrop.sinRangoDeFolios =>
          'Este equipo no tiene folios de visita. Sincroniza para que el '
              'servidor le asigne un rango.',
        MotivoNoNoDrop.sinFolios =>
          'Se acabaron los folios de visita. Sincroniza para pedir otro rango.',
        MotivoNoNoDrop.reprogramacionInvalida =>
          'La visita se reprograma a un día después de hoy y a lo más a un mes. '
              'Escoge otro día.',
      };
}

class NoDropSinIdentidad extends EstadoNoDrop {
  const NoDropSinIdentidad();
}

class ControladorNoDrop extends Notifier<EstadoNoDrop> {
  @override
  EstadoNoDrop build() => const NoDropInactivo();

  void registrar({
    required String clienteId,
    required String motivoCodigo,
    required Ubicacion? ubicacion,
    String? nota,
    String? reprogramadaPara,
  }) {
    final registro = ref.read(registroDeNoDropProvider);
    if (registro == null) {
      state = const NoDropSinIdentidad();
      return;
    }

    state = const NoDropEnCurso();
    try {
      final noDrop = registro.registrar(
        clienteId: clienteId,
        motivoCodigo: motivoCodigo,
        ubicacion: ubicacion,
        nota: nota,
        reprogramadaPara: reprogramadaPara,
      );
      ref.invalidate(resumenColaProvider);
      // Pasó y no le compraron: también es una visita hecha del plan. Y si se
      // reprogramó, la lista dice cuándo volver.
      ref.invalidate(clientesProvider);
      state = NoDropRegistrado(noDrop);
    } on NoDropRechazado catch (e) {
      state = NoDropFallido(e.motivo, e.detalle);
    }
  }

  void reiniciar() => state = const NoDropInactivo();
}

final noDropProvider = NotifierProvider<ControladorNoDrop, EstadoNoDrop>(
  ControladorNoDrop.new,
);
