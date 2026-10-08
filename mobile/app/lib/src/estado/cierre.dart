/// El cierre del día del vendedor: su corte y la carga que pide (ADR 0002 §82).
///
/// Las reglas viven en `dsd_core` (`RegistroDeCierre`); aquí solo se arma con
/// lo de la sesión y se refresca cuando la sincronización trae la respuesta de
/// la oficina.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import 'carrito.dart';
import 'mermas.dart';
import 'sesion.dart';

final registroDeCierreProvider = Provider<RegistroDeCierre?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;

  final dispositivoId = ref.watch(dispositivoIdProvider);
  if (dispositivoId == null) return null;

  return RegistroDeCierre(
    db: ref.watch(baseLocalProvider).db,
    outbox: ref.watch(outboxProvider),
    dispositivoId: dispositivoId,
    vendedor: sesion.credencial.nombre,
    codigoVendedor: sesion.credencial.codigo,
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: ref.watch(relojProvider),
  );
});

/// Sube cada vez que el vendedor hace su corte o pide carga, para que «Mi día»
/// se vuelva a leer.
final revisionDelCierreProvider = StateProvider<int>((_) => 0);

/// El corte de hoy y la carga pedida para mañana, como están en el teléfono.
class CierreDeHoy {
  const CierreDeHoy({this.corte, this.solicitud});

  final CorteDelVendedor? corte;
  final SolicitudDeCarga? solicitud;
}

final cierreDeHoyProvider = Provider<CierreDeHoy>((ref) {
  // La respuesta de la oficina llega con la sincronización, que sube esta
  // revisión al aplicar los deltas.
  ref.watch(revisionDelCamionProvider);
  ref.watch(revisionDelCierreProvider);
  final registro = ref.watch(registroDeCierreProvider);
  if (registro == null) return const CierreDeHoy();
  return CierreDeHoy(corte: registro.corteDelDia(), solicitud: registro.solicitudPara());
});

/// Compartir un ticket como texto: WhatsApp, correo, lo que tenga el teléfono.
///
/// Es una interfaz para que las pruebas no abran la hoja de compartir del
/// sistema: la reemplazan por una que solo anota lo que se compartió.
abstract class Compartidor {
  Future<void> compartir(String texto, {String? asunto});
}

class CompartidorDelSistema implements Compartidor {
  const CompartidorDelSistema();

  @override
  Future<void> compartir(String texto, {String? asunto}) async {
    await SharePlus.instance.share(ShareParams(text: texto, subject: asunto));
  }
}

final compartidorProvider = Provider<Compartidor>((_) => const CompartidorDelSistema());
