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
      'cliente', 'cartera', 'lista_precios', 'carga', 'motivo_merma',
      'motivo_no_drop']));
  });

  test('la carga del servidor real llena el camión', () {
    // Es el delta que el aplicador tiraba: se aceptaba para no ensuciar
    // `deltas_desconocidos` y no hacía nada. El teléfono sabía que le habían
    // cargado el camión y no qué.
    //
    // Que venga del fixture y no de un delta escrito a mano importa más aquí que
    // en ningún otro caso: el detalle lo construye un disparador de PostgreSQL
    // con `jsonb_agg`, y adivinar los tipos que salen de ahí es justo el error
    // que este contrato existe para no cometer.
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final fila = db.select('SELECT * FROM existencias_camion').single;
    expect(fila['cant_cargada'], equals(240.0));
    expect(fila['cant_actual'], equals(240.0));

    // Y queda fijada la carga activa, que es lo que el carrito estampa en cada
    // venta para que la liquidación pueda cuadrar el día.
    expect(
      db
          .select("SELECT valor FROM sync_estado WHERE clave = 'carga_id_activa'")
          .single['valor'],
      equals(fila['carga_id']),
    );
  });

  test('el detalle de la carga trae la cantidad como STRING de tres decimales', () {
    // A diferencia del precio —que viaja como número porque sale de un `to_jsonb`
    // crudo—, el detalle de la carga lo construye el disparador a mano y sí
    // cumple contracts/README.md §1.4. Si alguien lo cambiara a número, esto
    // falla aquí y no seis meses después en una liquidación que no cuadra.
    final carga = _deltas.firstWhere((d) => d.entidad == 'carga');
    final detalle = (carga.payload!['detalle'] as List).cast<Map<String, Object?>>();
    expect(detalle.single['cantidad'], equals('240.000'));
  });

  test('el fixture NO trae ningún borrador de carga', () {
    // Un borrador es una lista que alguien está armando en la oficina. Si llegara
    // al teléfono, el vendedor vería —y podría vender— mercancía que la bodega no
    // le entregó.
    final cargas = _deltas.where((d) => d.entidad == 'carga').toList();
    expect(cargas.length, equals(1));
    expect(cargas.single.payload!['estado'], equals('confirmada'));
  });

  test('la lista de precios por omisión llega y se guarda', () {
    // El delta que permite cotizarle a un cliente recién dado de alta en la
    // calle: ese cliente nace sin lista —la asigna el servidor al confirmarlo—
    // y el catálogo cae a la de omisión. Sin esta fila, el vendedor no le podría
    // vender al cliente que acaba de registrar.
    //
    // El servidor lo emitía desde el primer día, pero la lista sembrada por la
    // migración 0009 no tenía renglón en change_log (los triggers llegaron en la
    // 0010) y el dispositivo tiraba la entidad. Migración 0013.
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final lista = db
        .select('SELECT * FROM listas_precios WHERE es_default = 1')
        .single;
    expect(lista['codigo'], equals('GENERAL'));
    // El booleano de PostgreSQL llega como true y se guarda como 1.
    expect(lista['es_default'], equals(1));
    expect(lista['activo'], equals(1));
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

  test('la cartera del piloto se sigue aplicando sin romper nada', () {
    // Ya no hay crédito (ADR 0002 §81), pero un servidor con historia todavía
    // puede publicar un delta de cartera: el teléfono lo aplica al espejo y nada
    // lo lee para decidir una venta.
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final c = db.select('SELECT saldo_cache, saldo_cache_en FROM clientes').single;
    expect(c['saldo_cache'], equals(1200.0));
    expect(c['saldo_cache_en'], equals('2026-09-28T10:00:00.000Z'));
  });

  test('los motivos de merma llegan con lo que decide si se le descuenta', () {
    // Sin este delta, la pantalla de merma se abre con la lista vacía y el
    // vendedor no puede registrar la caja que se le reventó: la pérdida acaba
    // como faltante suyo.
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final fila = db.select(
      "SELECT nombre, afecta_vendedor, activo FROM motivos_merma "
      "WHERE codigo = 'ROTO'",
    ).single;
    expect(fila['nombre'], equals('Empaque roto'));
    // El servidor manda booleanos de JSON, no 0/1: es el tipo que el aplicador
    // tiene que convertir, y adivinarlo es el error que este contrato evita.
    expect(fila['afecta_vendedor'], equals(1));
    expect(fila['activo'], equals(1));

    final registro = RegistroDeMerma(
      db: db,
      vendedorId: 'VEND01',
      dispositivoId: 'equipo-1',
      outbox: Outbox(db),
      folios: RepoFolios(db),
      nuevoUuid: () => 'id-1',
      ahora: () => DateTime.parse('2026-09-28T10:00:00.000Z'),
    );
    expect(
      registro.motivos().map((m) => m.codigo),
      contains('ROTO'),
    );
  });

  test('los motivos de no-drop llegan en el orden de la oficina', () {
    AplicadorDeltas(db).aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final fila = db.select(
      "SELECT nombre, categoria, requiere_nota, orden, activo "
      "FROM motivos_no_drop WHERE codigo = 'CERRADO'",
    ).single;
    expect(fila['nombre'], equals('Cerrado'));
    // La categoría es lo que después permite preguntar cuántas visitas perdidas
    // son culpa nuestra.
    expect(fila['categoria'], equals('cliente'));
    expect(fila['requiere_nota'], equals(0));
    // `orden` entero: en la calle, con el cliente esperando, un catálogo
    // alfabético obliga a leer diez opciones para encontrar "cerrado".
    expect(fila['orden'], equals(10));
    expect(fila['activo'], equals(1));
  });

  test('un motivo desactivado por la oficina deja de ofrecerse', () {
    // El delta de desactivación es un upsert con `activo: false`. Si el aplicador
    // lo ignorara, el vendedor seguiría viendo el motivo que la oficina retiró y
    // escogería uno que el servidor va a marcar sin que él hiciera nada mal.
    final aplicador = AplicadorDeltas(db);
    aplicador.aplicar(_deltas, recibidoEn: '2026-09-28T10:00:00.000Z');

    final original = _deltas.firstWhere((d) => d.entidad == 'motivo_merma');
    aplicador.aplicar(
      [
        Delta(
          cursor: 9999,
          entidad: 'motivo_merma',
          entidadId: original.entidadId,
          operacion: 'upsert',
          payload: {...original.payload!, 'activo': false},
        ),
      ],
      recibidoEn: '2026-09-28T11:00:00.000Z',
    );

    expect(
      db.select(
        "SELECT activo FROM motivos_merma WHERE codigo = 'ROTO'",
      ).single['activo'],
      equals(0),
    );
    final registro = RegistroDeMerma(
      db: db,
      vendedorId: 'VEND01',
      dispositivoId: 'equipo-1',
      outbox: Outbox(db),
      folios: RepoFolios(db),
      nuevoUuid: () => 'id-1',
      ahora: () => DateTime.parse('2026-09-28T10:00:00.000Z'),
    );
    expect(registro.motivos().map((m) => m.codigo), isNot(contains('ROTO')));
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
