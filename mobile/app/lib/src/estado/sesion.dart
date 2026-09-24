/// Sesión y dependencias de la app.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/almacen_seguro.dart';
import '../datos/base_local.dart';
import '../datos/repo_clientes.dart';
import '../datos/repo_credencial.dart';

/// Se sobrescriben en las pruebas y en el arranque real.
final almacenSeguroProvider = Provider<AlmacenSeguro>(
  (_) => throw UnimplementedError('se define en el arranque'),
);
final baseLocalProvider = Provider<BaseLocal>(
  (_) => throw UnimplementedError('se define en el arranque'),
);

final repoCredencialProvider = Provider<RepoCredencial>(
  (ref) => RepoCredencial(ref.watch(almacenSeguroProvider)),
);
final repoClientesProvider = Provider<RepoClientes>(
  (ref) => RepoClientes(ref.watch(baseLocalProvider).db),
);
final outboxProvider = Provider<Outbox>(
  (ref) => Outbox(ref.watch(baseLocalProvider).db),
);

/// Reloj inyectable: las pruebas de vigencia necesitan mover el tiempo, y usar
/// `DateTime.now()` directo las volvería dependientes del día en que corren.
final relojProvider = Provider<DateTime Function()>((_) => DateTime.now);

/// Estado de la sesión.
sealed class Sesion {
  const Sesion();
}

class SinSesion extends Sesion {
  const SinSesion({this.motivo});

  /// Por qué no hay sesión. Se muestra al vendedor: "conéctate para renovar"
  /// es muy distinto de "PIN incorrecto".
  final ResultadoLogin? motivo;
}

class SesionAbierta extends Sesion {
  const SesionAbierta(this.credencial);

  final CredencialLocal credencial;
}

class ControladorSesion extends Notifier<Sesion> {
  @override
  Sesion build() => const SinSesion();

  /// Login sin señal contra la credencial guardada.
  ///
  /// Es el camino normal: el vendedor arranca su día en la bodega, muchas veces
  /// sin datos. El login online solo hace falta la primera vez y cuando la
  /// credencial vence.
  Future<ResultadoLogin> entrarOffline(String password) async {
    final credencial = await ref.read(repoCredencialProvider).leer();
    final resultado = intentarLoginOffline(
      credencial,
      password,
      ahora: ref.read(relojProvider)(),
    );

    state = resultado == ResultadoLogin.ok
        ? SesionAbierta(credencial!)
        : SinSesion(motivo: resultado);
    return resultado;
  }

  void salir() => state = const SinSesion();
}

final sesionProvider = NotifierProvider<ControladorSesion, Sesion>(
  ControladorSesion.new,
);

/// Lista de clientes filtrada. Lee de SQLite, así que responde igual sin señal.
final busquedaClientesProvider = StateProvider<String>((_) => '');

final clientesProvider = Provider<List<ClienteEnRuta>>((ref) {
  final busqueda = ref.watch(busquedaClientesProvider);
  return ref.watch(repoClientesProvider).deLaRuta(busqueda: busqueda);
});

/// Lo que hay sin sincronizar. Alimenta el indicador que el vendedor ve
/// siempre: un número de pendientes que crece es la señal de que algo va mal.
final resumenColaProvider = Provider<ResumenCola>(
  (ref) => ref.watch(outboxProvider).resumen(),
);
