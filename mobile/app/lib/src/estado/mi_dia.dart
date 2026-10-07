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
///
/// Escucha la cola (`resumenColaProvider`) y el camión: los dos se avisan al
/// vender, cobrar, mermar y al terminar cada sincronización. Bug de campo,
/// octubre 2026: sin esto, el provider se quedaba con la primera lectura y la
/// venta seguía «sin subir» en rojo hasta cerrar y abrir la app, aunque el
/// panel ya la tuviera.
final miDiaProvider = Provider<MiDia>(
  (ref) => ref.watch(miDiaDelProvider(ref.watch(diaOperativoProvider))),
);

/// El corte de CUALQUIER día, para revisar días anteriores desde «Mi día».
///
/// Pedido en operación (octubre 2026): «que al vendedor en Mi día lo deje
/// cambiar de día para revisar días anteriores». El teléfono no borra los
/// documentos de días pasados, así que el corte de ayer se arma igual que el de
/// hoy, sin señal. Escucha lo mismo que el de hoy: una venta de ayer que sube
/// tarde cambia su marca de «sin subir».
final miDiaDelProvider = Provider.family<MiDia, String>((ref, dia) {
  ref.watch(resumenColaProvider);
  ref.watch(revisionDelCamionProvider);
  return ref.watch(repoMiDiaProvider).delDia(dia);
});
