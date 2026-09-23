/// Sobres de visita: lo que el dispositivo encola y manda.
///
/// Espejo de `server/app/domain/sync/sobres.py`. Un sobre agrupa todo lo de
/// una visita —alta de cliente, venta, cobro— y el servidor lo aplica en una
/// sola transacción. Es también la llave de idempotencia: reenviarlo no
/// duplica nada.
library;

import 'canonico.dart';

class OperacionLocal {
  const OperacionLocal({
    required this.tipo,
    required this.entidadId,
    required this.datos,
  });

  final String tipo;
  final String entidadId;
  final Map<String, Object?> datos;

  Map<String, Object?> aMapa() => {
        'tipo': tipo,
        'entidad_id': entidadId,
        'datos': datos,
      };
}

class SobreLocal {
  SobreLocal({
    required this.operacionId,
    required this.secuencia,
    required this.operaciones,
    this.visitaId,
  }) : assert(operaciones.isNotEmpty, 'un sobre vacío no tiene sentido');

  final String operacionId;
  final int secuencia;
  final List<OperacionLocal> operaciones;
  final String? visitaId;

  /// El hash que verifica el servidor. Se calcula sobre la MISMA estructura
  /// que recompone `hash_de_sobre` en Python.
  String get hash => hashPayload({
        'operacion_id': operacionId,
        'visita_id': visitaId,
        'operaciones': operaciones.map((o) => o.aMapa()).toList(),
      });

  Map<String, Object?> aMapa() => {
        'operacion_id': operacionId,
        'secuencia': secuencia,
        'hash_payload': hash,
        'visita_id': visitaId,
        'operaciones': operaciones.map((o) => o.aMapa()).toList(),
      };
}
