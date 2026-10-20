/// ¿La hora del teléfono está bien? (ADR 0002 §157)
///
/// Con la hora del teléfono se decide de qué día es cada venta, sin señal y sin
/// preguntarle a nadie. Si está mal —la zona equivocada, el reloj a mano—, las
/// ventas de la noche caen en otro día y el corte no las ve. El servidor mide
/// cuánto va mal en cada envío y el teléfono lo guarda; aquí se lee.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'sesion.dart';
import 'sincronizacion.dart';

/// A partir de cuánto se avisa: diez minutos ya no son la red.
const desfaseQueSeAvisa = Duration(minutes: 10);

/// Cuánto va mal el reloj del teléfono, en segundos (positivo, atrasado), según
/// el último envío; nulo si nunca se midió. Se vuelve a leer al sincronizar.
final desfaseDeRelojProvider = Provider.autoDispose<int?>((ref) {
  ref.watch(syncProvider);
  final filas = ref.watch(baseLocalProvider).db.select(
    "SELECT valor FROM sync_estado WHERE clave = 'desfase_reloj_seg'",
  );
  return filas.isEmpty ? null : int.tryParse(filas.single['valor'] as String? ?? '');
});

/// «va 1 h 5 min atrasada», para el aviso; nulo si no hay que avisar.
String? relojDesfasadoEnPalabras(int? segundos) {
  if (segundos == null || segundos.abs() < desfaseQueSeAvisa.inSeconds) return null;
  final minutos = (segundos.abs() / 60).round();
  final cuanto = minutos >= 60
      ? '${minutos ~/ 60} h${minutos % 60 == 0 ? '' : ' ${minutos % 60} min'}'
      : '$minutos min';
  return 'va $cuanto ${segundos > 0 ? 'atrasada' : 'adelantada'}';
}
