/// Las pantallas del dashboard dentro de la app de la oficina: el cliente de
/// `/v1/panel` (ADR 0002 §194).
///
/// «Solo quiero poder mover el almacén, agregar productos, ese tipo de cosas»
/// desde la app. La pestaña Almacén pone los botones que el servidor le dice
/// —cuáles, y a qué pantalla lleva cada uno—, así que cambiar el dashboard no
/// pide otra APK. Para abrir una, la app pide un boleto de un solo uso y el
/// dashboard lo canjea por su sesión: no se teclea la contraseña otra vez.
library;

import 'dart:convert';

import 'oficina_rechazo.dart';
import 'transporte.dart';

/// Un botón: una pantalla del dashboard.
class AtajoDelPanel {
  const AtajoDelPanel({
    required this.titulo,
    required this.descripcion,
    required this.ruta,
    required this.icono,
  });

  factory AtajoDelPanel.deJson(Map<String, Object?> j) => AtajoDelPanel(
        titulo: j['titulo']! as String,
        descripcion: j['descripcion'] as String? ?? '',
        ruta: j['ruta']! as String,
        icono: j['icono'] as String? ?? '',
      );

  final String titulo;
  final String descripcion;

  /// La pantalla del dashboard, «/panel/…».
  final String ruta;

  /// Un nombre que la app convierte en su ícono.
  final String icono;
}

/// Para abrir la sesión del dashboard: sirve una vez y un minuto.
class BoletoDelPanel {
  const BoletoDelPanel({required this.boleto, required this.entrar});

  factory BoletoDelPanel.deJson(Map<String, Object?> j) => BoletoDelPanel(
        boleto: j['boleto']! as String,
        entrar: j['entrar'] as String? ?? '/panel/entrar/app',
      );

  final String boleto;

  /// Dónde se canjea: «/panel/entrar/app».
  final String entrar;
}

class ClienteDelPanel {
  const ClienteDelPanel(this._transporte);

  final Transporte _transporte;

  /// Los botones de esta persona; vacío si no le tocan.
  Future<List<AtajoDelPanel>> atajos() async {
    final r = await _transporte.obtener('/v1/panel/atajos');
    revisarRespuestaDeOficina(r);
    return [
      for (final a in jsonDecode(r.cuerpo) as List)
        AtajoDelPanel.deJson((a as Map).cast()),
    ];
  }

  Future<BoletoDelPanel> boleto() async {
    final r = await _transporte.post('/v1/panel/boleto', const {});
    revisarRespuestaDeOficina(r);
    return BoletoDelPanel.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }
}
