/// El corte del día del vendedor.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'sesion.dart';

final repoMiDiaProvider = Provider<RepoMiDia>(
  (ref) => RepoMiDia(ref.watch(baseLocalProvider).db),
);

/// El día operativo tal como lo estampan los documentos.
///
/// Se deriva igual que en `cobro.dart` y `venta.dart` —los diez primeros
/// caracteres del instante en ISO— y eso NO es casual: si esta pantalla calculara
/// el día de otra forma, mostraría un corte distinto del que la liquidación va a
/// cobrar, y la diferencia aparecería justo en los documentos del límite del día.
final diaOperativoProvider = Provider<String>(
  (ref) => ref.watch(relojProvider)().toUtc().toIso8601String().substring(0, 10),
);

/// Lo vendido y lo cobrado hoy, leído de SQLite: funciona sin señal.
final miDiaProvider = Provider<MiDia>(
  (ref) => ref.watch(repoMiDiaProvider).delDia(ref.watch(diaOperativoProvider)),
);
