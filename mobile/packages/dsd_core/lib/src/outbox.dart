/// Outbox: la cola del dispositivo.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA GARANTÍA
/// ─────────────────────────────────────────────────────────────────────────
/// El documento de negocio y su registro en la cola se escriben en la **misma
/// transacción de SQLite**. No hay un instante en que exista uno sin el otro.
///
/// Sin eso, un teléfono que se apaga entre las dos escrituras deja una venta
/// que nunca se va a sincronizar —el cliente tiene su papel y la oficina no se
/// entera jamás— o un registro en la cola apuntando a una venta que no existe.
/// Los dos casos descuadran, y ninguno se detecta hasta la liquidación.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA COLA NUNCA SE ATORA
/// ─────────────────────────────────────────────────────────────────────────
/// Un sobre que el servidor rechaza pasa a `cuarentena` y **deja de estorbar**.
/// Si un sobre malo bloqueara la cola, el vendedor se quedaría sin poder
/// vender por un dato que nadie puede corregir desde la calle.
library;

import 'dart:convert';

import 'package:sqlite3/sqlite3.dart';

import 'sobre.dart';

enum EstadoSobre { pendiente, enviando, confirmada, cuarentena }

class SobreEnCola {
  const SobreEnCola({
    required this.operacionId,
    required this.tipo,
    required this.entidadId,
    required this.secuencia,
    required this.payload,
    required this.hashPayload,
    required this.intentos,
  });

  final String operacionId;
  final String tipo;
  final String entidadId;
  final int secuencia;
  final Map<String, Object?> payload;
  final String hashPayload;
  final int intentos;
}

class ResumenCola {
  const ResumenCola({
    required this.pendientes,
    required this.enCuarentena,
    required this.masAntiguo,
  });

  final int pendientes;
  final int enCuarentena;

  /// Fecha del sobre pendiente más viejo. Es lo que decide si se puede abrir
  /// un día nuevo: no se arranca una carga con la cola del día anterior sin
  /// sincronizar.
  final String? masAntiguo;

  bool get todoSincronizado => pendientes == 0;
}

/// Repositorio de la cola sobre SQLite.
class Outbox {
  Outbox(this._db);

  final Database _db;

  /// Escribe el documento y su entrada en la cola **atómicamente**.
  ///
  /// [escribirNegocio] recibe la misma conexión, dentro de la transacción ya
  /// abierta. Si lanza, no queda nada: ni el documento ni la cola.
  void encolar(
    SobreLocal sobre, {
    required void Function(Database db) escribirNegocio,
    required String creadoEn,
  }) {
    _db.execute('BEGIN IMMEDIATE');
    try {
      escribirNegocio(_db);

      final payload = jsonEncode(sobre.aMapa());
      _db.execute(
        '''
        INSERT INTO outbox (operacion_id, tipo, entidad_id, payload, hash_payload,
                            secuencia, visita_id, estado, intentos, creado_en)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pendiente', 0, ?)
        ''',
        [
          sobre.operacionId,
          sobre.operaciones.map((o) => o.tipo).join('+'),
          sobre.operaciones.first.entidadId,
          payload,
          sobre.hash,
          sobre.secuencia,
          sobre.visitaId,
          creadoEn,
        ],
      );
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }

  /// Toma los siguientes sobres en orden FIFO estricto.
  ///
  /// El orden importa: un cobro que referencia una venta creada offline tiene
  /// que viajar después de ella.
  List<SobreEnCola> siguienteLote({int limite = 50}) {
    final filas = _db.select(
      '''
      SELECT operacion_id, tipo, entidad_id, secuencia, payload, hash_payload, intentos
        FROM outbox
       WHERE estado = 'pendiente'
       ORDER BY secuencia
       LIMIT ?
      ''',
      [limite],
    );
    return filas
        .map(
          (f) => SobreEnCola(
            operacionId: f['operacion_id'] as String,
            tipo: f['tipo'] as String,
            entidadId: f['entidad_id'] as String,
            secuencia: f['secuencia'] as int,
            payload: jsonDecode(f['payload'] as String) as Map<String, Object?>,
            hashPayload: f['hash_payload'] as String,
            intentos: f['intentos'] as int,
          ),
        )
        .toList();
  }

  /// Marca como confirmados los sobres que el servidor aceptó **o declaró
  /// duplicados**. Para el dispositivo son lo mismo: ya están del otro lado.
  void confirmar(List<String> operacionIds, {required String confirmadoEn}) {
    if (operacionIds.isEmpty) return;
    _db.execute('BEGIN IMMEDIATE');
    try {
      final marcadores = List.filled(operacionIds.length, '?').join(',');
      _db.execute(
        "UPDATE outbox SET estado='confirmada', confirmado_en=? "
        'WHERE operacion_id IN ($marcadores)',
        [confirmadoEn, ...operacionIds],
      );
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }

  /// Saca de la cola un sobre que el servidor rechazó.
  ///
  /// El payload íntegro ya quedó en la cuarentena del servidor; aquí solo se
  /// deja de reintentar. Mantenerlo en la cola lo volvería un tapón.
  void aCuarentena(String operacionId, String error) {
    _db.execute(
      "UPDATE outbox SET estado='cuarentena', ultimo_error=? WHERE operacion_id=?",
      [error, operacionId],
    );
  }

  /// Registra un fallo de transporte: el sobre sigue pendiente y se reintenta
  /// más tarde con espera creciente.
  void reintentarDespues(String operacionId, String error, {required String proximoIntento}) {
    _db.execute(
      "UPDATE outbox SET intentos = intentos + 1, ultimo_error = ?, "
      'proximo_intento = ? WHERE operacion_id = ?',
      [error, proximoIntento, operacionId],
    );
  }

  ResumenCola resumen() {
    final fila = _db.select('''
      SELECT (SELECT COUNT(*) FROM outbox WHERE estado='pendiente')  AS pendientes,
             (SELECT COUNT(*) FROM outbox WHERE estado='cuarentena') AS cuarentena,
             (SELECT MIN(creado_en) FROM outbox WHERE estado='pendiente') AS mas_antiguo
    ''').first;
    return ResumenCola(
      pendientes: fila['pendientes'] as int,
      enCuarentena: fila['cuarentena'] as int,
      masAntiguo: fila['mas_antiguo'] as String?,
    );
  }

  /// El siguiente número de secuencia. FIFO global del dispositivo.
  int siguienteSecuencia() {
    final fila = _db.select('SELECT COALESCE(MAX(secuencia), -1) + 1 AS s FROM outbox').first;
    return fila['s'] as int;
  }
}
