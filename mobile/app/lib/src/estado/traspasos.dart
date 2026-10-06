/// El estado de la devolución de mercancía a la bodega.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ EL VENDEDOR LA CAPTURA Y NO LA OFICINA
/// ─────────────────────────────────────────────────────────────────────────
/// Él es el dueño del almacén de origen y el único que sabe que acaba de bajar 18
/// cajas. Exigirle conexión para registrarlo haría que lo apuntara en papel, que
/// es exactamente cómo se pierde un sistema. Es el mismo trato que una merma.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE DEJA DE SER SUYO NO ES LO QUE DECLARÓ, ES LO QUE CONTARON
/// ─────────────────────────────────────────────────────────────────────────
/// La mercancía sale del camión al capturar y entra a la bodega cuando alguien la
/// cuenta. En medio está en **tránsito**, y lo que no cuadre se queda ahí: es una
/// diferencia con nombre y con fecha en vez de una confianza invisible.
///
/// Por eso esta pantalla muestra el estado de cada devolución —y lo contado, cuando
/// ya hay— y no solo un «enviado». Es el comprobante del vendedor.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'carrito.dart';
import 'mermas.dart';
import 'sesion.dart';

final registroDeTraspasoProvider = Provider<RegistroDeTraspaso?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;

  final dispositivoId = ref.watch(dispositivoIdProvider);
  if (dispositivoId == null) return null;

  return RegistroDeTraspaso(
    db: ref.watch(baseLocalProvider).db,
    dispositivoId: dispositivoId,
    outbox: ref.watch(outboxProvider),
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: ref.watch(relojProvider),
    almacenId: ref.watch(almacenDelVendedorProvider),
  );
});

/// Las devoluciones de este teléfono, lo más reciente primero.
final traspasosRecientesProvider = Provider<List<TraspasoEnLista>>(
  (ref) => ref.watch(registroDeTraspasoProvider)?.recientes() ?? const [],
);

sealed class EstadoTraspaso {
  const EstadoTraspaso();
}

class TraspasoInactivo extends EstadoTraspaso {
  const TraspasoInactivo();
}

class TraspasoEnCurso extends EstadoTraspaso {
  const TraspasoEnCurso();
}

class TraspasoRegistrado extends EstadoTraspaso {
  const TraspasoRegistrado(this.traspaso);

  final TraspasoGuardado traspaso;
}

class TraspasoFallido extends EstadoTraspaso {
  const TraspasoFallido(this.motivo);

  final MotivoNoTraspaso motivo;

  String get mensaje => switch (motivo) {
        MotivoNoTraspaso.sinRenglones =>
          'Escoge al menos un producto y cuánto bajaste.',
        MotivoNoTraspaso.sinAlmacen =>
          'Este equipo todavía no tiene camión asignado. Sincroniza para que el '
              'servidor le diga cuál es.',
      };
}

class TraspasoSinIdentidad extends EstadoTraspaso {
  const TraspasoSinIdentidad();
}

class ControladorTraspaso extends Notifier<EstadoTraspaso> {
  @override
  EstadoTraspaso build() => const TraspasoInactivo();

  void registrar({
    required List<RenglonDeTraspaso> renglones,
    String? observaciones,
  }) {
    final registro = ref.read(registroDeTraspasoProvider);
    if (registro == null) {
      state = const TraspasoSinIdentidad();
      return;
    }

    state = const TraspasoEnCurso();
    try {
      final traspaso = registro.registrar(
        renglones: renglones,
        observaciones: observaciones,
      );
      // El camión bajó: el catálogo y las existencias tienen que reflejarlo antes
      // de la siguiente venta, o le ofrecería al cliente mercancía que acaba de
      // dejar en la bodega.
      elCamionCambio(ref);
      ref.invalidate(traspasosRecientesProvider);
      ref.invalidate(resumenColaProvider);
      state = TraspasoRegistrado(traspaso);
    } on TraspasoRechazado catch (e) {
      state = TraspasoFallido(e.motivo);
    }
  }

  void reiniciar() => state = const TraspasoInactivo();
}

final traspasoProvider = NotifierProvider<ControladorTraspaso, EstadoTraspaso>(
  ControladorTraspaso.new,
);
