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
/// Con `diaOperativoDe`, la MISMA función que estampa la venta, el cobro, la
/// merma y el no-drop. Si esta pantalla calculara el día de otra forma, mostraría
/// un corte distinto del que la liquidación va a cobrar.
///
/// Bug de campo, octubre 2026: aquí se usaba el día UTC (`toUtc()` y los diez
/// primeros caracteres) cuando los documentos ya se estampaban con el día LOCAL.
/// En el centro de México, de las 18:00 en adelante el día UTC ya es mañana, así
/// que «Mi día» buscaba las ventas de mañana: el vendedor vendía, la venta llegaba
/// al servidor, y su corte no la mostraba.
final diaOperativoProvider = Provider<String>(
  (ref) => diaOperativoDe(ref.watch(relojProvider)()),
);

/// Lo vendido y lo cobrado hoy, leído de SQLite: funciona sin señal.
final miDiaProvider = Provider<MiDia>(
  (ref) => ref.watch(repoMiDiaProvider).delDia(ref.watch(diaOperativoProvider)),
);
