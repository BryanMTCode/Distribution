/// Sincronización: qué se hace con cada fallo.
///
/// La tabla de decisiones de `sincronizador.dart` es el diseño de esta fase.
/// Equivocarse en un renglón se paga con una cola atorada o con ventas
/// perdidas, así que cada renglón tiene su prueba.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

import 'ayudas_sync.dart';

void main() {
  late Database db;
  late Outbox outbox;

  setUp(() {
    db = baseLocal();
    outbox = Outbox(db);
  });

  tearDown(() => db.dispose());

  int contar(String tabla) =>
      db.select('SELECT COUNT(*) AS n FROM $tabla').first['n'] as int;

  // =========================================================================
  // PUSH · la tabla de decisiones
  // =========================================================================

  test('aceptada: el sobre sale de la cola', () async {
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote()),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.sobresConfirmados, equals(1));
    expect(outbox.resumen().pendientes, equals(0));
    expect(r.fin, equals(FinDeSync.completa));
  });

  test('duplicada: para el dispositivo es lo mismo que aceptada', () async {
    // Ya estaba del otro lado. El reenvío fue inofensivo, que es el objetivo
    // del diseño: no hay nada que corregir.
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote(), estado: 'duplicada'),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.sobresConfirmados, equals(1));
    expect(outbox.resumen().pendientes, equals(0));
  });

  test('rechazada: a cuarentena, y la cola sigue avanzando', () async {
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([
      Responde.push(
        sobres: outbox.siguienteLote(),
        estado: 'rechazada',
        errorCodigo: 'payload_invalido',
      ),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.sobresEnCuarentena, equals(1));
    expect(outbox.resumen().pendientes, equals(0), reason: 'quedó tapando la cola');
    expect(outbox.resumen().enCuarentena, equals(1));
  });

  test('error de red: nada se pierde y se reintenta', () async {
    // No se sabe si el servidor lo aplicó. Descartar "por si acaso" perdería
    // ventas ya cobradas; por eso el servidor tiene que ser idempotente.
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([const SeCaeLaRed()]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.fin, equals(FinDeSync.sinRed));
    expect(outbox.resumen().pendientes, equals(1));
    expect(outbox.siguienteLote().single.intentos, equals(1));
  });

  test('reintentar tras un corte manda el mismo sobre, no uno nuevo', () async {
    // Es lo que permite que la idempotencia del servidor funcione.
    encolarAlta(outbox, db, 'c1');
    final idOriginal = outbox.siguienteLote().single.operacionId;

    final primero = TransporteFalso([const SeCaeLaRed()]);
    await armarSincronizador(db, primero).sincronizar(cursorActual: 0);

    final segundo = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote()),
    ]);
    await armarSincronizador(db, segundo).sincronizar(cursorActual: 0);

    final enviado = (segundo.cuerposEnviados.first['sobres'] as List).first
        as Map<String, Object?>;
    expect(enviado['operacion_id'], equals(idOriginal));
  });

  test('sesión inválida: se detiene y NO toca la cola', () async {
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([const Responde(401, '{"detail":"expirado"}')]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.fin, equals(FinDeSync.sesionInvalida));
    expect(outbox.resumen().pendientes, equals(1));
    expect(outbox.resumen().enCuarentena, equals(0));
  });

  test('lote inválido (422): a cuarentena el lote completo', () async {
    // El único caso en que se descarta sin haber aplicado: reenviar lo mismo
    // fallaría igual, y reintentar para siempre dejaría al vendedor sin vender.
    encolarAlta(outbox, db, 'c1', secuencia: 0);
    encolarAlta(outbox, db, 'c2', secuencia: 1);
    final transporte = TransporteFalso([
      const Responde(422, 'el lote repite un operacion_id'),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.sobresEnCuarentena, equals(2));
    expect(outbox.resumen().pendientes, equals(0));
  });

  test('servidor caído (500): sigue pendiente, se reintenta', () async {
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([const Responde(503, 'mantenimiento')]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.fin, equals(FinDeSync.servidorCaido));
    expect(outbox.resumen().pendientes, equals(1));
    expect(outbox.resumen().enCuarentena, equals(0));
  });

  test('un lote con aceptadas y rechazadas separa cada sobre', () async {
    encolarAlta(outbox, db, 'c1', secuencia: 0);
    encolarAlta(outbox, db, 'c2', secuencia: 1);
    final lote = outbox.siguienteLote();

    final transporte = TransporteFalso([
      Responde(
        200,
        '{"lote_id":"l","aceptadas":1,"duplicadas":0,"rechazadas":1,"resultados":['
        '{"operacion_id":"${lote[0].operacionId}","estado":"aceptada"},'
        '{"operacion_id":"${lote[1].operacionId}","estado":"rechazada",'
        '"error_codigo":"payload_invalido","error_mensaje":"falta el nombre"}]}',
      ),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.sobresConfirmados, equals(1));
    expect(r.sobresEnCuarentena, equals(1));
    expect(outbox.resumen().pendientes, equals(0));
  });

  test('una cola grande se manda en tandas', () async {
    for (var i = 0; i < 7; i++) {
      encolarAlta(outbox, db, 'c$i', secuencia: i);
    }
    // El servidor falso refleja lo que recibe, así que la prueba no necesita
    // predecir cómo se cortan las tandas.
    final transporte = TransporteFalso(
      [const AceptaLoEnviado()],
      repiteUltimo: true,
    );

    final sincronizador = armarSincronizador(db, transporte, maxSobresPorLote: 3);
    final r = await sincronizador.sincronizar(cursorActual: 0);

    // Tres tandas de push (3 + 3 + 1), no una sola gigante.
    expect(transporte.llamadas.where((l) => l.contains('push')).length, equals(3));
    expect(r.sobresConfirmados, equals(7));
    expect(outbox.resumen().pendientes, equals(0));
  });

  // =========================================================================
  // PULL
  // =========================================================================

  test('los deltas se aplican y el cursor avanza', () async {
    final transporte = TransporteFalso([
      Responde.pull(cambios: [deltaProducto(10, 'p1', 'FRIJOL-1KG')]),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.deltasAplicados, equals(1));
    expect(r.cursor, equals(10));
    expect(contar('productos'), equals(1));
  });

  test('aplicar el mismo delta dos veces es inofensivo', () async {
    // El pull puede repetirse tras un corte; todo es upsert.
    final cambios = [deltaProducto(10, 'p1', 'FRIJOL-1KG')];

    await armarSincronizador(db, TransporteFalso([Responde.pull(cambios: cambios)]))
        .sincronizar(cursorActual: 0);
    await armarSincronizador(db, TransporteFalso([Responde.pull(cambios: cambios)]))
        .sincronizar(cursorActual: 0);

    expect(contar('productos'), equals(1));
  });

  test('con hay_mas se sigue pidiendo hasta el final', () async {
    final transporte = TransporteFalso([
      Responde.pull(cambios: [deltaProducto(1, 'p1', 'A')], hayMas: true),
      Responde.pull(cambios: [deltaProducto(2, 'p2', 'B')], hayMas: true),
      Responde.pull(cambios: [deltaProducto(3, 'p3', 'C')]),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.deltasAplicados, equals(3));
    expect(r.cursor, equals(3));
    expect(contar('productos'), equals(3));
  });

  test('una entidad desconocida se guarda y el cursor avanza', () async {
    // Atorar el cursor dejaría al equipo sin recibir NADA más; descartar en
    // silencio sería pérdida invisible.
    final transporte = TransporteFalso([
      Responde.pull(cambios: [
        deltaProducto(1, 'p1', 'A'),
        {
          'cursor': 2,
          'entidad': 'entidad_del_futuro',
          'entidad_id': 'x1',
          'operacion': 'upsert',
          'payload': {'algo': 'nuevo'},
        },
      ]),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(r.deltasAplicados, equals(1));
    expect(r.deltasDesconocidos, equals(1));
    expect(r.cursor, equals(2), reason: 'el cursor se atoró');
    expect(contar('deltas_desconocidos'), equals(1));
  });

  test('el delta de cartera es lo que hace funcionar el crédito local',
      () async {
    // Sin él, el dispositivo recibiría el límite pero nunca el saldo, y le
    // diría al vendedor que un cliente endeudado tiene toda su línea libre.
    final transporte = TransporteFalso([
      Responde.pull(cambios: [
        deltaCliente(1, 'c1'),
        deltaCartera(2, 'c1', saldo: '4800.00', limite: '5000.00'),
      ]),
    ]);

    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    final fila = db.select(
      'SELECT saldo_cache, limite_credito, saldo_cache_en FROM clientes WHERE id = ?',
      ['c1'],
    ).first;
    expect(fila['saldo_cache'], equals(4800.0));
    expect(fila['limite_credito'], equals(5000.0));
    expect(fila['saldo_cache_en'], equals(ahoraFijo));

    // Y la regla de crédito ya lo ve.
    final repo = RepoDePrueba(db);
    expect(repo.disponible('c1'), equals('200.00'));
  });

  test('el delta de cliente no pisa el saldo con cero', () async {
    // El payload de `clientes` no trae saldo. Escribirlo desde ahí lo pondría
    // en cero justo después de que la cartera lo actualizó.
    final transporte = TransporteFalso([
      Responde.pull(cambios: [
        deltaCartera(1, 'c1', saldo: '4800.00'),
        deltaCliente(2, 'c1', nombre: 'Nombre corregido'),
      ]),
    ]);
    // La cartera llega antes que el cliente: no hay fila que actualizar todavía.
    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);
    expect(contar('clientes'), equals(1));

    // Segunda vuelta: ahora sí existe y la cartera se aplica.
    final segundo = TransporteFalso([
      Responde.pull(cambios: [deltaCartera(3, 'c1', saldo: '4800.00')]),
    ]);
    await armarSincronizador(db, segundo).sincronizar(cursorActual: 2);

    final fila = db.select(
      'SELECT nombre_comercial, saldo_cache FROM clientes WHERE id = ?',
      ['c1'],
    ).first;
    expect(fila['nombre_comercial'], equals('Nombre corregido'));
    expect(fila['saldo_cache'], equals(4800.0));
  });

  test('un cliente confirmado por el servidor deja de ser alta local', () async {
    encolarAlta(outbox, db, 'c1');
    expect(db.select('SELECT es_local FROM clientes').first['es_local'], equals(1));

    final transporte = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote()),
      Responde.pull(cambios: [deltaCliente(1, 'c1')]),
    ]);
    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    final fila = db.select('SELECT es_local, sincronizado FROM clientes').first;
    expect(fila['es_local'], equals(0));
    expect(fila['sincronizado'], equals(1));
  });

  test('un producto retirado se desactiva, no se borra', () async {
    // Puede seguir apareciendo en ventas ya hechas que aún no sincronizan.
    await armarSincronizador(
      db,
      TransporteFalso([Responde.pull(cambios: [deltaProducto(1, 'p1', 'A')])]),
    ).sincronizar(cursorActual: 0);

    await armarSincronizador(
      db,
      TransporteFalso([
        Responde.pull(cambios: [
          {
            'cursor': 2,
            'entidad': 'producto',
            'entidad_id': 'p1',
            'operacion': 'delete',
            'payload': null,
          },
        ]),
      ]),
    ).sincronizar(cursorActual: 1);

    final fila = db.select('SELECT activo FROM productos WHERE id = ?', ['p1']).first;
    expect(fila['activo'], equals(0));
    expect(contar('productos'), equals(1));
  });

  // =========================================================================
  // Orden y reintentos
  // =========================================================================

  test('primero sale lo del vendedor, después entra lo del servidor', () async {
    // Si se trajeran los deltas primero, una actualización de precios podría
    // pisar el espejo mientras la venta hecha con el precio viejo sigue en la
    // cola.
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote()),
      Responde.pull(cambios: [deltaProducto(1, 'p1', 'A')]),
    ]);

    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(transporte.llamadas.first, contains('push'));
    expect(transporte.llamadas[1], contains('pull'));
  });

  test('sin red en el push no se intenta el pull', () async {
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([const SeCaeLaRed()]);

    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    expect(transporte.llamadas.where((l) => l.contains('pull')), isEmpty);
  });

  test('la espera entre intentos crece y tiene techo', () {
    // Sin techo, tras una noche sin señal el siguiente intento caería a horas
    // de distancia y el vendedor saldría a ruta con la cola del día anterior.
    expect(esperaPorIntentos(0), equals(const Duration(seconds: 15)));
    expect(esperaPorIntentos(1), equals(const Duration(seconds: 30)));
    expect(esperaPorIntentos(3), equals(const Duration(minutes: 2)));
    expect(esperaPorIntentos(20), equals(const Duration(minutes: 10)));
    expect(esperaPorIntentos(1000), equals(const Duration(minutes: 10)));
  });

  test('una sincronización sin nada que hacer no reporta actividad', () async {
    final r = await armarSincronizador(db, TransporteFalso([]))
        .sincronizar(cursorActual: 5);
    expect(r.huboActividad, isFalse);
    expect(r.fin, equals(FinDeSync.completa));
  });
}

/// Mínimo repositorio para comprobar que la regla de crédito ve el saldo que
/// dejó el delta de cartera.
class RepoDePrueba {
  RepoDePrueba(this.db);

  final Database db;

  String disponible(String clienteId) {
    final f = db.select(
      'SELECT limite_credito, saldo_cache, permite_credito, bloqueado '
      'FROM clientes WHERE id = ?',
      [clienteId],
    ).first;
    final estado = EstadoCredito(
      limite: Dinero.deTexto((f['limite_credito'] as num).toStringAsFixed(2)),
      saldoConfirmado: Dinero.deTexto((f['saldo_cache'] as num).toStringAsFixed(2)),
      permiteCredito: (f['permite_credito'] as int) == 1,
      bloqueado: (f['bloqueado'] as int) == 1,
    );
    return estado.disponible.texto;
  }
}
