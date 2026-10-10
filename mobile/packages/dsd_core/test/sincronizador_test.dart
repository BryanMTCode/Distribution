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

  test('el lote_id que viaja es un UUID, porque el servidor lo exige así',
      () async {
    // ESTA PRUEBA NO EXISTÍA, Y POR ESO NADA LLEGABA AL SERVIDOR EN PRODUCCIÓN.
    //
    // El id de lote se construía como `'lote-$operacionId'`, y el esquema del
    // servidor declara `lote_id: uuid.UUID`. Pydantic rechazaba ese prefijo con un
    // 422 ANTES de ejecutar una línea del dominio, y ese camino no deja rastro:
    // `sync_cuarentena` vacía, `ultimo_push` sin tocar —el panel decía
    // «push: nunca»— y el teléfono mandando el lote entero a su cuarentena local.
    // El vendedor veía «1 con error» y la venta no aparecía en el tablero.
    //
    // Las pruebas de aquí no lo veían porque el transporte falso acepta cualquier
    // texto —su propia respuesta traía `"lote_id":"l"`— y las del servidor armaban
    // sus payloads con UUID válidos. Nadie cruzaba los dos lados.
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote()),
    ]);

    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    final enviado = transporte.cuerposEnviados.first['lote_id'] as String;
    expect(
      enviado,
      matches(RegExp(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
      )),
      reason: 'el servidor declara lote_id como UUID: cualquier otra forma es un '
          '422 que no deja rastro en ninguna tabla',
    );
  });

  test('el lote_id es el MISMO si se reintenta el mismo tramo', () async {
    // Es la propiedad que el id derivado del contenido existe para dar: tras un
    // corte, el servidor tiene que reconocer el lote en vez de duplicarlo.
    encolarAlta(outbox, db, 'c1');

    final primero = TransporteFalso([const SeCaeLaRed()]);
    await armarSincronizador(db, primero).sincronizar(cursorActual: 0);

    final segundo = TransporteFalso([
      Responde.push(sobres: outbox.siguienteLote()),
    ]);
    await armarSincronizador(db, segundo).sincronizar(cursorActual: 0);

    expect(
      segundo.cuerposEnviados.first['lote_id'],
      equals(primero.cuerposEnviados.first['lote_id']),
    );
  });

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

  test('un delta de cartera del piloto se aplica al espejo sin romper nada',
      () async {
    // Ya no hay crédito (ADR 0002 §81), pero un servidor con historia todavía
    // puede publicar uno. Se aplica al espejo y nada lo lee para vender.
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
  });

  test('la cartera del piloto trae lo por confirmar', () async {
    final transporte = TransporteFalso([
      Responde.pull(cambios: [
        deltaCliente(1, 'c1'),
        deltaCartera(2, 'c1', saldo: '4800.00', porConfirmar: '800.00'),
      ]),
    ]);
    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    final fila = db.select(
      'SELECT saldo_cache, por_confirmar FROM clientes WHERE id = ?',
      ['c1'],
    ).first;
    expect(fila['saldo_cache'], equals(4800.0));
    expect(fila['por_confirmar'], equals(800.0));

    // La oficina lo confirmó: el saldo baja y lo por confirmar vuelve a cero.
    final despues = TransporteFalso([
      Responde.pull(cambios: [
        deltaCartera(3, 'c1', saldo: '4000.00', porConfirmar: '0.00'),
      ]),
    ]);
    await armarSincronizador(db, despues).sincronizar(cursorActual: 2);
    final confirmada = db.select(
      'SELECT saldo_cache, por_confirmar FROM clientes WHERE id = ?',
      ['c1'],
    ).first;
    expect(confirmada['por_confirmar'], equals(0.0));
  });

  test('una cartera de un servidor viejo, sin por_confirmar, deja cero', () async {
    final transporte = TransporteFalso([
      Responde.pull(cambios: [deltaCliente(1, 'c1'), deltaCartera(2, 'c1')]),
    ]);
    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);
    final fila = db.select(
      'SELECT por_confirmar FROM clientes WHERE id = ?',
      ['c1'],
    ).first;
    expect(fila['por_confirmar'], equals(0.0));
  });

  test('EL PLAN DE VISITA LLEGA DENTRO DEL DELTA DEL CLIENTE', () async {
    // Sin entidad nueva en la sincronización: el disparador de la 0041 copia el
    // plan a `clientes.plan_visita` y viaja con el cliente.
    final delta = deltaCliente(1, 'c1');
    (delta['payload']! as Map<String, Object?>)['plan_visita'] = [
      {'dia': 1, 'semana': null},
      {'dia': 4, 'semana': 2},
    ];
    await armarSincronizador(db, TransporteFalso([
      Responde.pull(cambios: [delta]),
    ])).sincronizar(cursorActual: 0);

    final guardado = db
        .select("SELECT plan_visita FROM clientes WHERE id = 'c1'")
        .single['plan_visita'] as String?;
    expect(
      leerPlanDeVisita(guardado),
      equals(const [DiaDeVisita(1), DiaDeVisita(4, 2)]),
    );
  });

  test('un cliente de un servidor viejo, sin plan, queda sin plan', () async {
    await armarSincronizador(db, TransporteFalso([
      Responde.pull(cambios: [deltaCliente(1, 'c1')]),
    ])).sincronizar(cursorActual: 0);
    final guardado = db
        .select("SELECT plan_visita FROM clientes WHERE id = 'c1'")
        .single['plan_visita'];
    expect(guardado, isNull);
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

  test('LA FAMILIA LLEGA COMO SU PROPIO DELTA, y se borra si la borran', () async {
    // ADR 0002 §92: el producto trae su categoria_id; el nombre y el orden de
    // la familia viajan aparte, para agrupar el catálogo en el teléfono.
    Map<String, Object?> familia(int cursor, String operacion, {String nombre = 'Botanas'}) => {
          'cursor': cursor,
          'entidad': 'categoria',
          'entidad_id': 'f-1',
          'operacion': operacion,
          'payload': operacion == 'delete'
              ? null
              : {'id': 'f-1', 'codigo': 'BOTANAS', 'nombre': nombre, 'orden': 2,
                 'activo': true, 'padre_id': null},
        };

    final r = await armarSincronizador(
      db,
      TransporteFalso([
        Responde.pull(cambios: [familia(1, 'upsert'), familia(2, 'upsert', nombre: 'Botanas Javi')]),
      ]),
    ).sincronizar(cursorActual: 0);
    expect(r.deltasDesconocidos, 0, reason: 'el teléfono no sabía qué hacer con la familia');
    expect(
      db.select('SELECT nombre, orden, activo FROM familias').single,
      {'nombre': 'Botanas Javi', 'orden': 2, 'activo': 1},
    );

    await armarSincronizador(
      db,
      TransporteFalso([Responde.pull(cambios: [familia(3, 'delete')])]),
    ).sincronizar(cursorActual: 2);
    expect(contar('familias'), 0);
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
  // La profundidad de cola que el teléfono reporta
  // =========================================================================
  // Es el único dato del sistema que SOLO el teléfono puede dar, y el cierre del
  // día descansa en él: sin esto, la liquidación tiene que pedirle a una persona
  // que jure que el equipo terminó de subir todo, y ese juramento queda guardado
  // como si fuera un hecho.

  test('el push declara cuántos sobres quedan después de la tanda', () async {
    for (var i = 0; i < 5; i++) {
      encolarAlta(outbox, db, 'c$i');
    }
    // Tandas de dos: la primera deja tres.
    final transporte = TransporteFalso(
      [const AceptaLoEnviado()],
      repiteUltimo: true,
    );

    await armarSincronizador(db, transporte, maxSobresPorLote: 2)
        .sincronizar(cursorActual: 0);

    final declarados = transporte.cuerposEnviados
        .where((c) => c.containsKey('sobres'))
        .map((c) => c['cola_pendiente'])
        .toList();
    // 5 → 3 → 1 → 0. El último push vacía la cola.
    expect(declarados, equals([3, 1, 0]));
  });

  test('es un entero sin comillas, no un texto', () async {
    // `contracts/README.md` §1.4: los conteos van como entero de JSON. Mandarlo
    // como texto haría que el servidor rechazara el lote entero por un campo que
    // no tiene nada que ver con la venta.
    encolarAlta(outbox, db, 'c1');
    final transporte = TransporteFalso([const AceptaLoEnviado()]);

    await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

    final enviado = transporte.cuerposEnviados.first['cola_pendiente'];
    expect(enviado, isA<int>());
    expect(enviado, equals(0));
  });

  test('una tanda que acaba en cuarentena también vacía la cola', () async {
    // Los rechazados salen de 'pendiente' igual que los aceptados, así que el
    // número declarado sigue valiendo. Si contara los rechazados como pendientes,
    // la liquidación quedaría bloqueada para siempre por un sobre que el servidor
    // ya decidió no aplicar.
    encolarAlta(outbox, db, 'c1');
    encolarAlta(outbox, db, 'c2');
    final transporte = TransporteFalso([
      const AceptaLoEnviado(
        estado: 'rechazada',
        errorCodigo: 'payload_invalido',
      ),
    ]);

    await armarSincronizador(db, transporte, maxSobresPorLote: 2)
        .sincronizar(cursorActual: 0);

    expect(transporte.cuerposEnviados.first['cola_pendiente'], equals(0));
    expect(outbox.resumen().pendientes, equals(0));
  });

  test('un corte de red no declara una cola vacía', () async {
    // El sobre se queda, así que lo que se declaró en el intento fallido no puede
    // haber dicho que no quedaba nada: el servidor lo habría guardado como "al
    // día" y la liquidación habría cerrado sobre un equipo con ventas sin subir.
    encolarAlta(outbox, db, 'c1');
    encolarAlta(outbox, db, 'c2');
    final transporte = TransporteFalso([const SeCaeLaRed()]);

    final r = await armarSincronizador(db, transporte, maxSobresPorLote: 1)
        .sincronizar(cursorActual: 0);

    expect(r.fin, equals(FinDeSync.sinRed));
    // Declaró 1 —el que no iba en la tanda— y el push ni llegó, así que el
    // servidor no guardó nada. La cola real sigue en 2.
    expect(transporte.cuerposEnviados.first['cola_pendiente'], equals(1));
    expect(outbox.resumen().pendientes, equals(2));
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

    // Las órdenes del servidor van antes que todo (Fase 9): de ahí sale si el
    // equipo tiene que borrarse, y si está suspendido no debe intentar el pull.
    expect(transporte.llamadas.first, contains('/v1/dispositivos/mio'));

    // Y entre los dos viajes de DATOS, el push primero.
    final datos = transporte.llamadas
        .where((l) => !l.contains('dispositivos/mio'))
        .toList();
    expect(datos[0], contains('push'));
    expect(datos[1], contains('pull'));
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
