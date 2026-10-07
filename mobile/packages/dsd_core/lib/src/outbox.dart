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

  /// Nada que mostrar: ni cola por subir ni nada con error.
  ///
  /// La cuarentena CUENTA, y no contaba. Con `pendientes == 0` a secas, un
  /// teléfono con una venta en cuarentena y nada pendiente ocultaba la barra: el
  /// vendedor no veía el «1 con error» ni el botón para reintentarlo, y la única
  /// señal de que una venta no llegó era que no aparecía en el tablero de la
  /// oficina. Es el estado exacto en que quedó un equipo tras el fallo del
  /// `lote_id`.
  bool get todoSincronizado => pendientes == 0 && enCuarentena == 0;
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
      _marcarDocumentos();
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }

  /// Marca como subidos los documentos cuyo sobre el servidor ya confirmó.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// BUG DE CAMPO, OCTUBRE 2026
  /// ───────────────────────────────────────────────────────────────────────
  /// `confirmar` sacaba el sobre de la cola y nadie tocaba el documento: la
  /// venta se quedaba con `sincronizada = 0` para siempre. «Mi día» decía «1
  /// documento no ha subido, sincroniza antes de entregar» de una venta que la
  /// oficina ya tenía en el panel, y el vendedor no tenía forma de quitarlo.
  ///
  /// Se marca por cada operación DENTRO del sobre —uno de visita trae venta y
  /// cobro—, y recorre todos los confirmados, no solo los de esta tanda: así
  /// también se corrigen los que se confirmaron con la versión anterior.
  void marcarDocumentosConfirmados() {
    _db.execute('BEGIN IMMEDIATE');
    try {
      _marcarDocumentos();
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }

  void _marcarDocumentos() {
    for (final (tipo, tabla, columna) in _documentos) {
      _db.execute(
        "UPDATE $tabla SET $columna = 1 "
        " WHERE $columna = 0 "
        "   AND id IN ("
        "     SELECT json_extract(op.value, '\$.entidad_id') "
        "       FROM outbox o, json_each(o.payload, '\$.operaciones') op "
        "      WHERE o.estado = 'confirmada' "
        "        AND json_extract(op.value, '\$.tipo') = ?"
        "   )",
        [tipo],
      );
    }
  }

  /// Qué documento deja cada tipo de operación, y su bandera de «ya subió».
  static const _documentos = [
    ('venta.crear', 'ventas', 'sincronizada'),
    ('cobro.crear', 'cobros', 'sincronizado'),
    ('merma.crear', 'mermas', 'sincronizada'),
    ('no_drop.crear', 'no_drops', 'sincronizado'),
    ('traspaso.crear', 'traspasos', 'sincronizado'),
    ('cliente.crear', 'clientes', 'sincronizado'),
  ];

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

  /// Devuelve a la cola lo que quedó en cuarentena, en su orden original.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// POR QUÉ ESTO TIENE QUE EXISTIR
  /// ───────────────────────────────────────────────────────────────────────
  /// `siguienteLote` solo toma sobres `pendiente`, así que hasta ahora un sobre
  /// en cuarentena estaba muerto: no había forma de reintentarlo desde el
  /// teléfono, nunca.
  ///
  /// Eso está bien cuando el rechazo es por los DATOS —una venta a un cliente que
  /// de verdad no existe no mejora reintentándola—, y está muy mal cuando el
  /// rechazo fue por un FALLO NUESTRO. Pasó: un `lote_id` que no era UUID hacía
  /// que el servidor contestara 422 a todo, y el teléfono mandó a cuarentena
  /// ventas y clientes perfectamente válidos. Arreglado el bug, esos documentos
  /// seguían sin poder subir — dinero capturado en la calle que no llegaba.
  ///
  /// Y arrastra un efecto peor: una venta a un cliente creado en el teléfono
  /// referencia un `cliente_id` que solo existe aquí. Si el alta del cliente cae
  /// en cuarentena y la venta no, la venta llega al servidor y es rechazada por
  /// «referencia a un cliente que no existe». El orden importa, y por eso esto
  /// reencola **respetando la secuencia**: el alta del cliente vuelve a viajar
  /// antes que su venta.
  ///
  /// Se reinician los intentos y se limpia el último error: lo que se reintenta
  /// es la operación, no su historial de fracasos.
  ///
  /// Devuelve cuántos sobres volvieron a la cola.
  int reencolarCuarentena() {
    final cuantos = _db
        .select("SELECT COUNT(*) AS n FROM outbox WHERE estado='cuarentena'")
        .first['n'] as int;
    if (cuantos == 0) return 0;
    _db.execute(
      "UPDATE outbox SET estado='pendiente', intentos=0, ultimo_error=NULL, "
      "proximo_intento=NULL WHERE estado='cuarentena'",
    );
    return cuantos;
  }

  /// Lo que quedó con error, con su motivo. Para poder decirle al vendedor QUÉ
  /// pasó en lugar de solo contarle cuántos.
  List<({String operacionId, String tipo, String? error})> enCuarentena() => _db
      .select(
        "SELECT operacion_id, tipo, ultimo_error FROM outbox "
        "WHERE estado='cuarentena' ORDER BY secuencia",
      )
      .map(
        (f) => (
          operacionId: f['operacion_id'] as String,
          tipo: f['tipo'] as String,
          error: f['ultimo_error'] as String?,
        ),
      )
      .toList();

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
