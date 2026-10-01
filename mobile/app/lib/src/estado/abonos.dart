/// El estado del abono: dinero que el cliente paga de lo que debía.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ "ABONO" Y NO "COBRO"
/// ─────────────────────────────────────────────────────────────────────────
/// En `estado/carrito.dart` ya existe un `cobroProvider`, y ahí "cobrar"
/// significa **cerrar la venta del carrito**. Si esto se llamara igual habría dos
/// cosas distintas con el mismo nombre en el mismo árbol de estado, y la que se
/// lee primero no sería la que se busca.
///
/// El documento sigue llamándose `cobro` en el dominio, en la base y en el
/// servidor —ahí no hay ambigüedad—. Lo que se renombra es el estado de pantalla.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'carrito.dart';
import 'sesion.dart';

/// Quién cobra y con qué equipo.
///
/// Sale de la credencial guardada, no de la pantalla: igual que en la venta, el
/// vendedor no elige a nombre de quién recibe el dinero.
final identidadDeCobroProvider = Provider<IdentidadDeCobro?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;
  final c = sesion.credencial;

  final db = ref.watch(baseLocalProvider).db;
  final filas = db.select(
    "SELECT valor FROM sync_estado WHERE clave = 'dispositivo_id'",
  );
  if (filas.isEmpty) return null;
  final dispositivoId = filas.single['valor'] as String?;
  if (dispositivoId == null) return null;

  return IdentidadDeCobro(
    vendedorId: c.usuarioId,
    codigoVendedor: c.codigo,
    dispositivoId: dispositivoId,
  );
});

final registroDeCobroProvider = Provider<RegistroDeCobro?>((ref) {
  final identidad = ref.watch(identidadDeCobroProvider);
  if (identidad == null) return null;

  final reloj = ref.watch(relojProvider);
  return RegistroDeCobro(
    db: ref.watch(baseLocalProvider).db,
    outbox: ref.watch(outboxProvider),
    folios: ref.watch(repoFoliosProvider),
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: () => reloj().toUtc().toIso8601String(),
    identidad: identidad,
  );
});

/// Cuántos folios de cobro le quedan al equipo.
///
/// Se muestra cuando quedan pocos: quedarse sin folios a media ruta deja al
/// vendedor sin poder recibir dinero, y la única salida es encontrar señal.
final foliosDeCobroProvider = Provider<RangoFolios?>(
  (ref) => ref.watch(repoFoliosProvider).leer('cobro'),
);

/// Lo que pasó al intentar registrar el abono.
sealed class EstadoAbono {
  const EstadoAbono();
}

class AbonoInactivo extends EstadoAbono {
  const AbonoInactivo();
}

class AbonoEnCurso extends EstadoAbono {
  const AbonoEnCurso();
}

class AbonoRegistrado extends EstadoAbono {
  const AbonoRegistrado(this.cobro);

  final CobroGuardado cobro;
}

class AbonoFallido extends EstadoAbono {
  const AbonoFallido(this.motivo, [this.detalle]);

  final MotivoNoCobro motivo;
  final String? detalle;

  /// Lo que el vendedor tiene que leer. Cada motivo pide algo distinto de él, y
  /// ninguno debe parecerse a los otros: "sincroniza" y "escribe la referencia"
  /// se resuelven de maneras que no tienen nada que ver.
  String get mensaje => switch (motivo) {
        MotivoNoCobro.importeInvalido =>
          'El importe tiene que ser mayor que cero.',
        MotivoNoCobro.faltaReferencia =>
          'Escribe la referencia: sin ella la oficina no puede encontrar el '
              'pago en el banco.',
        MotivoNoCobro.sinRangoDeFolios =>
          'Este equipo no tiene folios de cobro. Sincroniza para que el '
              'servidor le asigne un rango.',
        MotivoNoCobro.sinFolios =>
          'Se acabaron los folios de cobro. Sincroniza para pedir otro rango.',
      };
}

/// No se pudo ni intentar: falta la identidad del equipo.
class AbonoSinIdentidad extends EstadoAbono {
  const AbonoSinIdentidad();
}

class ControladorAbono extends Notifier<EstadoAbono> {
  @override
  EstadoAbono build() => const AbonoInactivo();

  /// Registra el abono del cliente.
  ///
  /// `saldoAntes` viaja al servidor como dato **forense**: es lo que este teléfono
  /// creía que debía el cliente. No decide nada —el servidor aplica el FIFO sobre
  /// la cartera real— pero permite explicar después por qué el vendedor cobró lo
  /// que cobró.
  void registrar({
    required String clienteId,
    required Dinero importe,
    FormaDePago formaDePago = FormaDePago.efectivo,
    String? referencia,
    Dinero? saldoAntes,
    Ubicacion? ubicacion,
  }) {
    final registro = ref.read(registroDeCobroProvider);
    if (registro == null) {
      state = const AbonoSinIdentidad();
      return;
    }

    state = const AbonoEnCurso();
    try {
      final cobro = registro.registrar(
        clienteId: clienteId,
        importe: importe,
        formaDePago: formaDePago,
        referencia: referencia,
        saldoAntes: saldoAntes,
        ubicacion: ubicacion,
      );
      // El saldo que ve el vendedor se compone restando los cobros encolados, así
      // que la lista de clientes cambia en cuanto este abono entra a la cola. Y la
      // cola tiene un sobre más por enviar.
      ref.invalidate(clientesProvider);
      ref.invalidate(resumenColaProvider);
      state = AbonoRegistrado(cobro);
    } on CobroRechazado catch (e) {
      state = AbonoFallido(e.motivo, e.detalle);
    }
  }

  void reiniciar() => state = const AbonoInactivo();
}

final abonoProvider = NotifierProvider<ControladorAbono, EstadoAbono>(
  ControladorAbono.new,
);
