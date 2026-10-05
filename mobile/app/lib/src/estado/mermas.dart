/// El estado de la merma y del no-drop.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LOS DOS DOCUMENTOS QUE EXPLICAN UNA DIFERENCIA
/// ─────────────────────────────────────────────────────────────────────────
/// Ninguno mueve dinero, y por eso es fácil tratarlos como papeleo. No lo son:
/// son los únicos que explican por qué el cierre no cuadra.
///
/// Sin la merma, la caja que se reventó en el camión llega a la liquidación como
/// faltante, y un faltante sin explicación **se le carga al vendedor**. Sin el
/// no-drop, un día de 20 visitas con 12 ventas se ve igual que uno de 12 visitas
/// con 12 ventas, y desde la oficina son indistinguibles.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL CATÁLOGO DE MOTIVOS VIENE DEL SERVIDOR, Y PUEDE ESTAR VACÍO
/// ─────────────────────────────────────────────────────────────────────────
/// Los dos motivos son catálogo cerrado (`motivos_merma`, `motivos_no_drop`), y
/// llegan por delta. Un equipo que nunca sincronizó los tiene vacíos, y entonces
/// **no hay nada que capturar**: la pantalla lo dice con esas palabras en vez de
/// mostrar un selector sin opciones, porque un desplegable vacío parece una falla
/// de la aplicación y no un equipo sin sincronizar.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/repo_catalogo.dart';
import 'carrito.dart';
import 'sesion.dart';

/// El camión de este vendedor, para que el servidor sepa de dónde sale la
/// mercancía mermada.
///
/// Sale de la credencial, no de la pantalla: dejar que el dispositivo lo declare
/// permitiría mermar el inventario de otro camión. El servidor además lo ignora y
/// usa el del contexto, así que esto es conveniencia, no autoridad.
final almacenDelVendedorProvider = Provider<String?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;
  return sesion.credencial.almacenId;
});

final registroDeMermaProvider = Provider<RegistroDeMerma?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;

  final dispositivoId = ref.watch(dispositivoIdProvider);
  if (dispositivoId == null) return null;

  final reloj = ref.watch(relojProvider);
  return RegistroDeMerma(
    db: ref.watch(baseLocalProvider).db,
    vendedorId: sesion.credencial.codigo,
    dispositivoId: dispositivoId,
    outbox: ref.watch(outboxProvider),
    folios: ref.watch(repoFoliosProvider),
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: reloj,
    almacenId: ref.watch(almacenDelVendedorProvider),
  );
});

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

/// Los motivos de merma sincronizados. Vacío = equipo sin sincronizar.
final motivosDeMermaProvider = Provider<List<MotivoDeMerma>>(
  (ref) => ref.watch(registroDeMermaProvider)?.motivos() ?? const [],
);

/// Los motivos de no-drop, en el orden que definió la oficina.
///
/// `orden` existe porque en la calle, con el cliente esperando, un catálogo
/// alfabético obliga a leer diez opciones para encontrar "cerrado".
final motivosDeNoDropProvider = Provider<List<MotivoDeNoDrop>>(
  (ref) => ref.watch(registroDeNoDropProvider)?.motivos() ?? const [],
);

/// Lo que trae el camión, para escoger qué se mermó.
final productosDelCamionProvider = Provider<List<ProductoDelCamion>>(
  (ref) => ref.watch(repoCatalogoProvider).enElCamion(),
);

final foliosDeMermaProvider = Provider<RangoFolios?>(
  (ref) => ref.watch(repoFoliosProvider).leer('merma'),
);

final foliosDeNoDropProvider = Provider<RangoFolios?>(
  (ref) => ref.watch(repoFoliosProvider).leer('no_drop'),
);

// ---------------------------------------------------------------------------
// La merma
// ---------------------------------------------------------------------------

sealed class EstadoMerma {
  const EstadoMerma();
}

class MermaInactiva extends EstadoMerma {
  const MermaInactiva();
}

class MermaEnCurso extends EstadoMerma {
  const MermaEnCurso();
}

class MermaRegistrada extends EstadoMerma {
  const MermaRegistrada(this.merma);

  final MermaGuardada merma;
}

class MermaFallida extends EstadoMerma {
  const MermaFallida(this.motivo, [this.detalle]);

  final MotivoNoMerma motivo;
  final String? detalle;

  /// Cada motivo pide algo distinto del vendedor, y ninguno debe parecerse a los
  /// otros: "sincroniza" y "escoge un producto" se resuelven de maneras que no
  /// tienen nada que ver.
  String get mensaje => switch (motivo) {
        MotivoNoMerma.sinRenglones =>
          'Escoge al menos un producto y cuánto se perdió.',
        MotivoNoMerma.cantidadInvalida =>
          'Hay un renglón en cero. Pon cuánto fue, o quítalo.',
        MotivoNoMerma.faltaCliente =>
          'Una devolución necesita cliente: sin él la oficina no puede '
              'revisarla contra su venta.',
        MotivoNoMerma.motivoDesconocido =>
          'Ese motivo ya no está en el catálogo. Sincroniza y escoge otro.',
        MotivoNoMerma.sinRangoDeFolios =>
          'Este equipo no tiene folios de merma. Sincroniza para que el '
              'servidor le asigne un rango.',
        MotivoNoMerma.sinFolios =>
          'Se acabaron los folios de merma. Sincroniza para pedir otro rango.',
      };
}

class MermaSinIdentidad extends EstadoMerma {
  const MermaSinIdentidad();
}

class ControladorMerma extends Notifier<EstadoMerma> {
  @override
  EstadoMerma build() => const MermaInactiva();

  void registrar({
    required TipoDeMerma tipo,
    required String motivoCodigo,
    required List<RenglonDeMerma> renglones,
    String? clienteId,
    String? observaciones,
    Ubicacion? ubicacion,
  }) {
    final registro = ref.read(registroDeMermaProvider);
    if (registro == null) {
      state = const MermaSinIdentidad();
      return;
    }

    state = const MermaEnCurso();
    try {
      final merma = registro.registrar(
        tipo: tipo,
        motivoCodigo: motivoCodigo,
        renglones: renglones,
        clienteId: clienteId,
        observaciones: observaciones,
        ubicacion: ubicacion,
      );
      // El inventario del camión cambió: el catálogo y las existencias que ve el
      // vendedor tienen que reflejarlo antes de la siguiente venta, o le ofrecería
      // mercancía que acaba de tirar.
      ref.invalidate(existenciasProvider);
      ref.invalidate(catalogoProvider);
      ref.invalidate(productosDelCamionProvider);
      ref.invalidate(resumenColaProvider);
      state = MermaRegistrada(merma);
    } on MermaRechazada catch (e) {
      state = MermaFallida(e.motivo, e.detalle);
    }
  }

  void reiniciar() => state = const MermaInactiva();
}

final mermaProvider = NotifierProvider<ControladorMerma, EstadoMerma>(
  ControladorMerma.new,
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
        MotivoNoNoDrop.faltaUbicacion =>
          'Falta la ubicación. Sin ella, esta visita no se distingue de una que '
              'nunca se hizo: espera el GPS o ajusta el punto a mano.',
        MotivoNoNoDrop.sinRangoDeFolios =>
          'Este equipo no tiene folios de visita. Sincroniza para que el '
              'servidor le asigne un rango.',
        MotivoNoNoDrop.sinFolios =>
          'Se acabaron los folios de visita. Sincroniza para pedir otro rango.',
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
      );
      ref.invalidate(resumenColaProvider);
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
