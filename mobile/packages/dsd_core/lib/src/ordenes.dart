/// Lo que el servidor le ordena al equipo (Fase 9).
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL ÚNICO CANAL SERVIDOR → DISPOSITIVO QUE NO SON DATOS
/// ─────────────────────────────────────────────────────────────────────────
/// Todo lo demás que baja del servidor son deltas: catálogo, precios, saldos.
/// Esto es otra cosa — una instrucción— y hace falta un canal propio por una
/// razón concreta y sencilla: **un equipo al que se le ordenó el borrado y que no
/// tiene nada en la cola nunca haría push**, así que nunca recibiría la orden
/// si viniera en la respuesta del push.
///
/// Por eso el sincronizador pregunta al EMPEZAR cada corrida. Son unos cientos
/// de bytes y resuelve también el otro aviso que no tenía dónde vivir: cuántos
/// días le quedan a la credencial local antes de exigir conexión.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA REGLA DEL BORRADO: NUNCA SE BORRA LO QUE NO SE HA ENTREGADO
/// ─────────────────────────────────────────────────────────────────────────
/// `borrar = true` **no** significa «bórrate ahora». Significa «bórrate cuando
/// termines de entregar». El motivo real de un borrado casi nunca es un robo
/// —es una renuncia, un cambio de teléfono, un equipo extraviado— y en los tres
/// casos el aparato puede traer dentro un día de ventas sin sincronizar.
///
/// Y cuando sí es un robo, borrar rápido no gana nada: la base está cifrada con
/// SQLCipher y su llave vive en el Keystore, detrás del PIN.
library;

/// Cuánto le queda al equipo antes de que el login offline deje de funcionar.
///
/// `null` cuando el equipo nunca ha sincronizado: «nunca» y «hoy» no son lo
/// mismo, y tratarlos igual haría ver como al día a un equipo recién registrado
/// que todavía no ha subido nada.
class VigenciaDelAcceso {
  const VigenciaDelAcceso({
    required this.diasMaxOffline,
    required this.diasSinSincronizar,
  });

  final int diasMaxOffline;
  final int? diasSinSincronizar;

  int? get diasRestantes =>
      diasSinSincronizar == null ? null : diasMaxOffline - diasSinSincronizar!;

  /// Si conviene avisarle al vendedor que se conecte.
  ///
  /// Dos días de margen: enterarse a las 6 de la mañana, en la bodega, con el
  /// camión cargado y sin señal, es un día de ruta perdido. El servidor lo sabe
  /// con días de antelación.
  bool get conviendeAvisar {
    final quedan = diasRestantes;
    return quedan != null && quedan <= 2;
  }

  bool get caducado {
    final quedan = diasRestantes;
    return quedan != null && quedan <= 0;
  }
}

class OrdenesDelServidor {
  const OrdenesDelServidor({
    required this.estado,
    required this.borrar,
    required this.borradoMotivo,
    required this.vigencia,
  });

  factory OrdenesDelServidor.deJson(Map<String, Object?> json) =>
      OrdenesDelServidor(
        estado: json['estado']! as String,
        borrar: json['borrar']! as bool,
        borradoMotivo: json['borrado_motivo'] as String?,
        vigencia: VigenciaDelAcceso(
          diasMaxOffline: json['dias_max_offline']! as int,
          diasSinSincronizar: json['dias_sin_sincronizar'] as int?,
        ),
      );

  /// 'activo' | 'suspendido' | 'revocado'
  final String estado;

  /// Hay orden de borrado pendiente. Se ejecuta DESPUÉS de entregar la cola.
  final bool borrar;
  final String? borradoMotivo;
  final VigenciaDelAcceso vigencia;

  /// Un equipo suspendido entrega y no recibe: puede hacer push y no pull.
  bool get soloEntrega => estado == 'suspendido';
}
