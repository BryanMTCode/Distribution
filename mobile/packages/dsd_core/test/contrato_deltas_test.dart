/// Contrato de la forma de los deltas, desde el lado del dispositivo.
///
/// `contracts/deltas_de_ejemplo.json` lo genera el servidor a partir de un pull
/// REAL (`server/tests/test_contrato_deltas.py`). Aquí el aplicador lo digiere.
///
/// Sin esta prueba, el aplicador se probaría contra deltas escritos a mano — es
/// decir, contra lo que yo *creo* que manda el servidor. Los tipos que salen de
/// `to_jsonb` no son los que uno supondría: los booleanos vienen como `true`,
/// no como 0/1, y los importes como número JSON, no como string.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

final _documento = jsonDecode(
  File('../../../contracts/deltas_de_ejemplo.json').readAsStringSync(),
) as Map<String, dynamic>;

List<Delta> get _deltas => (_documento['cambios'] as List)
    .cast<Map<String, dynamic>>()
    .map(
      (c) => Delta(
        cursor: c['cursor'] as int,
        entidad: c['entidad'] as String,
        entidadId: c['entidad_id'] as String,
        operacion: c['operacion'] as String,
        payload: (c['payload'] as Map?)?.cast<String, Object?>(),
      ),
    )
    .toList();

void main() {
  late Database db;

  setUp(() {
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
  });

  tearDown(() => db.dispose());

  test('el fixture trae las entidades que el dispositivo necesita', () {
    final entidades = _deltas.map((d) => d.entidad).toSet();
    expect(entidades, containsAll(['producto', 'producto_unidad', 'precio',
      'cliente', 'cartera']));
  });

  test('el aplicador digiere los deltas reales del servidor', () {
    final r = AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    expect(r.desconocidos, equals(0),
        reason: 'el servidor manda una entidad que esta app no sabe aplicar');
    expect(r.aplicados, equals(_deltas.length));
  });

  test('el producto queda con su código de barras y su unidad base', () {
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final p = db.select('SELECT * FROM productos').single;
    expect(p['sku'], equals('FRIJOL-1KG'));
    expect(p['codigo_barras'], equals('7501234567890'));
    expect(p['unidad_base'], equals('PZA'));
    // El booleano de PostgreSQL llega como true y se guarda como 1.
    expect(p['activo'], equals(1));
  });

  test('las dos presentaciones llegan con su factor', () {
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final unidades = {
      for (final f in db.select('SELECT unidad_codigo, factor, es_default '
          'FROM producto_unidades'))
        f['unidad_codigo'] as String: f,
    };
    expect(unidades.keys, containsAll(['PZA', 'CAJA']));
    expect(unidades['CAJA']!['factor'], equals(24.0));
    expect(unidades['PZA']!['es_default'], equals(1));
  });

  test('el precio llega con su versión', () {
    // La versión es lo que después delata una venta hecha con lista vieja.
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final precio = db.select('SELECT * FROM precios').single;
    expect(precio['precio'], equals(25.5));
    expect(precio['version'], equals(1));
  });

  test('el cliente llega con acentos, emoji y dirección compuesta', () {
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final c = db.select('SELECT * FROM clientes').single;
    expect(c['nombre_comercial'], equals('La Esquina de Ñoño 🏪'));
    expect(c['direccion'], equals('Av. Hidalgo 145 Centro'));
    expect(c['secuencia'], equals(3));
    expect(c['es_local'], equals(0));
    expect(c['sincronizado'], equals(1));
  });

  test('la cartera deja al crédito local listo para decidir', () {
    // El cierre del círculo: el saldo que manda el servidor, aplicado al
    // espejo, alimentando la regla de crédito que bloquea la venta.
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final c = db.select('SELECT limite_credito, saldo_cache, saldo_cache_en, '
        'permite_credito, bloqueado FROM clientes').single;

    final estado = EstadoCredito(
      limite: Dinero.deTexto((c['limite_credito'] as num).toStringAsFixed(2)),
      saldoConfirmado: Dinero.deTexto((c['saldo_cache'] as num).toStringAsFixed(2)),
      permiteCredito: (c['permite_credito'] as int) == 1,
      bloqueado: (c['bloqueado'] as int) == 1,
    );

    expect(estado.saldoConfirmado.texto, equals('1200.00'));
    expect(estado.disponible.texto, equals('3800.00'));
    expect(c['saldo_cache_en'], equals('2026-09-28T10:00:00.000Z'));

    // Una venta de 3800 a crédito cabe justo; una de 3800.01 ya no.
    expect(
      evaluarVenta(estado, Dinero.deTexto('3800.00'), aCredito: true).permitida,
      isTrue,
    );
    expect(
      evaluarVenta(estado, Dinero.deTexto('3800.01'), aCredito: true).permitida,
      isFalse,
    );
  });

  test('aplicar el fixture dos veces no duplica nada', () {
    final aplicador = AplicadorDeltas(db);
    aplicador.aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');
    aplicador.aplicar(_deltas, recibidoEn: '2026-09-28T10:05:00.000Z');

    expect(db.select('SELECT COUNT(*) AS n FROM productos').single['n'], equals(1));
    expect(db.select('SELECT COUNT(*) AS n FROM clientes').single['n'], equals(1));
    expect(
      db.select('SELECT COUNT(*) AS n FROM producto_unidades').single['n'],
      equals(2),
    );
  });
}
