/// Las pantallas del dashboard dentro de la app de la oficina (ADR 0002 §194).
///
/// «Solo quiero poder mover el almacén, agregar productos, ese tipo de cosas»
/// desde la app, sin actualizar la app de los vendedores. Los botones —cuáles y a
/// qué pantalla lleva cada uno— los decide el servidor; la app solo los pinta y
/// abre la pantalla con un boleto, así que cambiar el dashboard no pide otra APK.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/abridor_de_navegador.dart';
import 'sincronizacion.dart';

final clienteDelPanelProvider = Provider<ClienteDelPanel?>((ref) {
  final transporte = ref.watch(transporteProvider);
  return transporte == null ? null : ClienteDelPanel(transporte);
});

/// Los botones que el servidor le da a esta persona. Sin señal, o con un
/// servidor que todavía no los tiene, ninguno: la parte no aparece.
final atajosDelPanelProvider = FutureProvider<List<AtajoDelPanel>>((ref) async {
  final cliente = ref.watch(clienteDelPanelProvider);
  if (cliente == null) return const [];
  try {
    return await cliente.atajos();
  } on Exception {
    return const [];
  }
});

final abridorDeNavegadorProvider = Provider<AbridorDeNavegador>(
  (_) => const AbridorDeNavegadorDelSistema(),
);

/// Qué hacer con un enlace que se toca dentro del dashboard.
enum Destino {
  /// Una pantalla del dashboard: se abre ahí mismo.
  dentro,

  /// El Excel: se baja en el navegador del teléfono, que sabe guardarlo.
  descarga,

  /// Otro sitio: al navegador.
  afuera,

  /// «Salir»: se cierra la sesión del dashboard y se regresa a la app.
  salir,
}

Destino adondeVa(Uri url, Uri servidor) {
  final mismoServidor = url.scheme == servidor.scheme &&
      url.host == servidor.host &&
      url.port == servidor.port;
  if (!mismoServidor || !(url.path == '/panel' || url.path.startsWith('/panel/'))) {
    return Destino.afuera;
  }
  if (url.path == '/panel/salir') return Destino.salir;
  if (url.path.startsWith('/panel/reporte/excel')) return Destino.descarga;
  return Destino.dentro;
}

/// La dirección para abrir en el navegador del teléfono una pantalla del
/// dashboard: el navegador no tiene la sesión de la app, así que lleva su boleto.
Uri enElNavegador(Uri servidor, BoletoDelPanel boleto, Uri destino) => servidor.replace(
      path: boleto.entrar,
      queryParameters: {
        'boleto': boleto.boleto,
        'siguiente': destino.hasQuery ? '${destino.path}?${destino.query}' : destino.path,
      },
    );
