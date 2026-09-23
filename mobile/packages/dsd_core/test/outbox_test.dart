/// La cola del dispositivo.
///
/// La prueba que da nombre a toda la fase es `el documento y la cola se
/// escriben juntos o no se escribe nada`: es la garantía de que un teléfono
/// que se apaga a media venta no deja un ticket impreso que nunca llegará a la
/// oficina.
library;

import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _ahora = '2026-09-23T10:15:00.000Z';

Database abrirBaseLocal() {
  final db = sqlite3.openInMemory();
  db.execute(File('../../db/schema.sql').readAsStringSync());
  return db;
}

SobreLocal sobreDeCliente(String id, {int secuencia = 0, String nombre = 'Doña Mary'}) =>
    SobreLocal(
      operacionId: 'op-$id',
      secuencia: secuencia,
      visitaId: 'visita-$id',
      operaciones: [
        OperacionLocal(
          tipo: 'cliente.crear',
          entidadId: id,
          datos: {'nombre_comercial': nombre, 'ubicacion_origen': 'gps'},
        ),
      ],
    );

void Function(Database) escribirCliente(String id, String nombre) => (db) {
      db.execute(
        'INSERT INTO clientes (id, nombre_comercial, es_local, sincronizado) '
        'VALUES (?, ?, 1, 0)',
        [id, nombre],
      );
    };

void main() {
  late Database db;
  late Outbox outbox;

  setUp(() {
    db = abrirBaseLocal();
    outbox = Outbox(db);
  });

  tearDown(() => db.dispose());

  int contar(String tabla) =>
      db.select('SELECT COUNT(*) AS n FROM $tabla').first['n'] as int;

  // -------------------------------------------------------------------------
  // La garantía
  // -------------------------------------------------------------------------

  test('el documento y la cola se escriben juntos', () {
    outbox.encolar(
      sobreDeCliente('c1'),
      escribirNegocio: escribirCliente('c1', 'Doña Mary'),
      creadoEn: _ahora,
    );
    expect(contar('clientes'), equals(1));
    expect(contar('outbox'), equals(1));
  });

  test('si falla el documento, no queda nada en la cola', () {
    expect(
      () => outbox.encolar(
        sobreDeCliente('c1'),
        escribirNegocio: (db) => throw StateError('se fue la batería'),
        creadoEn: _ahora,
      ),
      throwsStateError,
    );
    expect(contar('clientes'), equals(0));
    expect(contar('outbox'), equals(0), reason: 'quedó un sobre sin su documento');
  });

  test('si falla la cola, no queda el documento', () {
    // Segundo encolado con el mismo operacion_id: la cola lo rechaza.
    outbox.encolar(
      sobreDeCliente('c1'),
      escribirNegocio: escribirCliente('c1', 'Doña Mary'),
      creadoEn: _ahora,
    );
    expect(
      () => outbox.encolar(
        sobreDeCliente('c1'),
        escribirNegocio: escribirCliente('c2', 'Otra tienda'),
        creadoEn: _ahora,
      ),
      throwsA(isA<SqliteException>()),
    );
    expect(contar('clientes'), equals(1), reason: 'se guardó un cliente sin sobre');
  });

  // -------------------------------------------------------------------------
  // FIFO
  // -------------------------------------------------------------------------

  test('la cola se drena en orden de secuencia', () {
    for (final i in [2, 0, 1]) {
      outbox.encolar(
        sobreDeCliente('c$i', secuencia: i),
        escribirNegocio: escribirCliente('c$i', 'Tienda $i'),
        creadoEn: _ahora,
      );
    }
    final lote = outbox.siguienteLote();
    expect(lote.map((s) => s.secuencia), equals([0, 1, 2]));
  });

  test('la secuencia avanza sola y no se repite', () {
    expect(outbox.siguienteSecuencia(), equals(0));
    outbox.encolar(
      sobreDeCliente('c1'),
      escribirNegocio: escribirCliente('c1', 'A'),
      creadoEn: _ahora,
    );
    expect(outbox.siguienteSecuencia(), equals(1));
  });

  // -------------------------------------------------------------------------
  // La cola nunca se atora
  // -------------------------------------------------------------------------

  test('un sobre en cuarentena deja de estorbar', () {
    for (var i = 0; i < 3; i++) {
      outbox.encolar(
        sobreDeCliente('c$i', secuencia: i),
        escribirNegocio: escribirCliente('c$i', 'Tienda $i'),
        creadoEn: _ahora,
      );
    }
    // El servidor rechaza el primero: sin sacarlo, taparía a los otros dos.
    outbox.aCuarentena('op-c0', 'payload_invalido');

    final lote = outbox.siguienteLote();
    expect(lote.map((s) => s.operacionId), equals(['op-c1', 'op-c2']));
    expect(outbox.resumen().enCuarentena, equals(1));
  });

  test('aceptada y duplicada se confirman igual', () {
    for (var i = 0; i < 2; i++) {
      outbox.encolar(
        sobreDeCliente('c$i', secuencia: i),
        escribirNegocio: escribirCliente('c$i', 'Tienda $i'),
        creadoEn: _ahora,
      );
    }
    // Para el dispositivo son lo mismo: ya están del otro lado.
    outbox.confirmar(['op-c0', 'op-c1'], confirmadoEn: _ahora);
    expect(outbox.siguienteLote(), isEmpty);
    expect(outbox.resumen().todoSincronizado, isTrue);
  });

  test('un fallo de transporte no saca el sobre de la cola', () {
    outbox.encolar(
      sobreDeCliente('c1'),
      escribirNegocio: escribirCliente('c1', 'A'),
      creadoEn: _ahora,
    );
    outbox.reintentarDespues('op-c1', 'sin señal', proximoIntento: _ahora);

    final lote = outbox.siguienteLote();
    expect(lote, hasLength(1));
    expect(lote.first.intentos, equals(1));
  });

  // -------------------------------------------------------------------------
  // La regla del día
  // -------------------------------------------------------------------------

  test('el resumen dice si se puede abrir un día nuevo', () {
    expect(outbox.resumen().todoSincronizado, isTrue);

    outbox.encolar(
      sobreDeCliente('c1'),
      escribirNegocio: escribirCliente('c1', 'A'),
      creadoEn: '2026-09-22T18:00:00.000Z',
    );
    final resumen = outbox.resumen();
    expect(resumen.todoSincronizado, isFalse);
    expect(resumen.masAntiguo, equals('2026-09-22T18:00:00.000Z'));
  });

  // -------------------------------------------------------------------------
  // El sobre que viaja
  // -------------------------------------------------------------------------

  test('el payload encolado es el que verifica el servidor', () {
    final sobre = sobreDeCliente('c1');
    outbox.encolar(
      sobre,
      escribirNegocio: escribirCliente('c1', 'Doña Mary'),
      creadoEn: _ahora,
    );
    final guardado = outbox.siguienteLote().single;
    expect(guardado.hashPayload, equals(sobre.hash));
    expect(guardado.payload['hash_payload'], equals(sobre.hash));
  });
}
