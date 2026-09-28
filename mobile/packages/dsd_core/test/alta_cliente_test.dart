/// Alta de cliente en la calle.
///
/// Dos cosas que se prueban aquí y no en otro lado: que el cliente y su sobre
/// se escriben juntos o no se escribe nada, y que el aviso de posible duplicado
/// llega ANTES de crearlo — en la calle el vendedor sabe si la tienda de al
/// lado es la misma; en la oficina, dos semanas después y con dos historiales
/// ya separados, nadie puede saberlo.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _ahora = '2026-09-28T10:15:00.000Z';

void main() {
  late Database db;
  late Outbox outbox;
  late AltaDeClientes altas;
  var contador = 0;

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    outbox = Outbox(db);
    altas = AltaDeClientes(
      db: db,
      outbox: outbox,
      nuevoUuid: () => 'id-${++contador}',
      ahora: () => _ahora,
    );
  });

  tearDown(() => db.dispose());

  Ubicacion enLaCalle({double lat = 19.4326, double lng = -99.1332, double? p = 8}) =>
      Ubicacion(lat: lat, lng: lng, origen: OrigenUbicacion.gps, precisionMetros: p);

  int contar(String tabla) =>
      db.select('SELECT COUNT(*) AS n FROM $tabla').single['n'] as int;

  // -------------------------------------------------------------------------
  // El alta
  // -------------------------------------------------------------------------

  test('el cliente y su sobre se escriben juntos', () {
    altas.registrar(
      DatosDeAlta(nombreComercial: 'Abarrotes Doña Mary', ubicacion: enLaCalle()),
    );
    expect(contar('clientes'), equals(1));
    expect(contar('outbox'), equals(1));
  });

  test('el sobre lleva la operación que el servidor sabe aplicar', () {
    altas.registrar(const DatosDeAlta(nombreComercial: 'Doña Mary'));

    final enCola = outbox.siguienteLote().single;
    final operaciones =
        (enCola.payload['operaciones']! as List).cast<Map<String, Object?>>();
    expect(operaciones.single['tipo'], equals('cliente.crear'));
    expect(enCola.payload['visita_id'], isNotNull);
    // El hash guardado es el que el servidor va a recalcular.
    expect(enCola.hashPayload, hasLength(64));
  });

  test('un alta sin nombre no procede', () {
    // Es el único campo sin el que el registro no sirve para nada.
    expect(
      () => altas.registrar(const DatosDeAlta(nombreComercial: '   ')),
      throwsArgumentError,
    );
    expect(contar('outbox'), equals(0));
  });

  test('el cliente nace sin línea de crédito', () {
    // ADR 0002: esa decisión es de la oficina. Aceptarla desde el dispositivo
    // sería dejar que el vendedor se autorice su propia cartera.
    altas.registrar(const DatosDeAlta(nombreComercial: 'Doña Mary'));

    final c = db.select('SELECT * FROM clientes').single;
    expect(c['permite_credito'], equals(0));
    expect(c['limite_credito'], equals(0));
    expect(c['es_local'], equals(1));
    expect(c['sincronizado'], equals(0));
  });

  test('la dirección se arma con lo que haya', () {
    altas.registrar(const DatosDeAlta(
      nombreComercial: 'Doña Mary',
      calle: ' Av. Hidalgo ',
      numero: '145',
      colonia: '',
    ));
    expect(
      db.select('SELECT direccion FROM clientes').single['direccion'],
      equals('Av. Hidalgo 145'),
    );
  });

  test('las referencias se guardan: en colonias sin nomenclatura son lo único',
      () {
    altas.registrar(const DatosDeAlta(
      nombreComercial: 'Doña Mary',
      referencias: 'Frente al parque, portón café',
    ));
    expect(
      db.select('SELECT referencias FROM clientes').single['referencias'],
      equals('Frente al parque, portón café'),
    );
  });

  // -------------------------------------------------------------------------
  // El GPS no bloquea
  // -------------------------------------------------------------------------

  test('se puede dar de alta sin ubicación', () {
    // Dentro de un mercado techado el GPS no entrega nada, y la tienda existe
    // igual. Si el alta lo exigiera, el vendedor simplemente no registraría.
    final id = altas.registrar(const DatosDeAlta(nombreComercial: 'Del mercado'));

    expect(id, isNotEmpty);
    final c = db.select('SELECT lat, lng, ubicacion_origen FROM clientes').single;
    expect(c['lat'], isNull);
    expect(c['ubicacion_origen'], isNull);
  });

  test('una lectura mala se guarda igual, con su precisión', () {
    altas.registrar(DatosDeAlta(
      nombreComercial: 'Entre dos edificios',
      ubicacion: enLaCalle(p: 180),
    ));
    final c = db.select('SELECT ubicacion_precision_m FROM clientes').single;
    expect(c['ubicacion_precision_m'], equals(180.0));
  });

  test('una coordenada corregida a mano queda marcada como tal', () {
    altas.registrar(DatosDeAlta(
      nombreComercial: 'Corregida',
      ubicacion: enLaCalle(p: 200).desplazada(norte: 40),
    ));
    final c = db.select('SELECT ubicacion_origen, ubicacion_precision_m '
        'FROM clientes').single;
    expect(c['ubicacion_origen'], equals('manual'));
    expect(c['ubicacion_precision_m'], isNull);
  });

  // -------------------------------------------------------------------------
  // Duplicados: atajarlos en la calle
  // -------------------------------------------------------------------------

  test('avisa de un cliente a la vuelta de la esquina', () {
    altas.registrar(
      DatosDeAlta(nombreComercial: 'Abarrotes Mary', ubicacion: enLaCalle()),
    );

    final cerca = altas.cercanos(enLaCalle().desplazada(norte: 25));
    expect(cerca, hasLength(1));
    expect(cerca.single.nombreComercial, equals('Abarrotes Mary'));
    expect(cerca.single.distanciaMetros, closeTo(25, 1));
  });

  test('no avisa de uno que está a tres cuadras', () {
    altas.registrar(
      DatosDeAlta(nombreComercial: 'Abarrotes Mary', ubicacion: enLaCalle()),
    );
    expect(altas.cercanos(enLaCalle().desplazada(norte: 300)), isEmpty);
  });

  test('los cercanos vienen del más próximo al más lejano', () {
    final base = enLaCalle();
    altas.registrar(DatosDeAlta(
        nombreComercial: 'La de enfrente', ubicacion: base.desplazada(norte: 10)));
    altas.registrar(DatosDeAlta(
        nombreComercial: 'La de la esquina', ubicacion: base.desplazada(norte: 50)));

    final cerca = altas.cercanos(base);
    expect(cerca.map((c) => c.nombreComercial),
        equals(['La de enfrente', 'La de la esquina']));
  });

  test('sin ubicación no se opina de duplicados', () {
    // Comparar por nombre daría falsos positivos constantes: "Abarrotes María"
    // hay uno por cuadra.
    altas.registrar(
      DatosDeAlta(nombreComercial: 'Abarrotes Mary', ubicacion: enLaCalle()),
    );
    expect(altas.cercanos(null), isEmpty);
  });

  test('los clientes sin coordenadas no estorban la búsqueda', () {
    altas.registrar(const DatosDeAlta(nombreComercial: 'Sin GPS'));
    expect(altas.cercanos(enLaCalle()), isEmpty);
  });

  // -------------------------------------------------------------------------
  // Varias altas en la misma ruta
  // -------------------------------------------------------------------------

  test('cada alta toma su propia secuencia en la cola', () {
    altas.registrar(const DatosDeAlta(nombreComercial: 'Una'));
    altas.registrar(const DatosDeAlta(nombreComercial: 'Otra'));

    final lote = outbox.siguienteLote();
    expect(lote.map((s) => s.secuencia), equals([0, 1]));
    expect(contar('clientes'), equals(2));
  });

  test('cada alta tiene su propio sobre y su propia visita', () {
    altas.registrar(const DatosDeAlta(nombreComercial: 'Una'));
    altas.registrar(const DatosDeAlta(nombreComercial: 'Otra'));

    final lote = outbox.siguienteLote();
    expect(lote[0].operacionId, isNot(equals(lote[1].operacionId)));
    expect(lote[0].payload['visita_id'], isNot(equals(lote[1].payload['visita_id'])));
  });
}
