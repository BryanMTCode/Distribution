/// La carga del camión, del lado del teléfono.
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ───────────────────────────────────────────────────────────────────────────
/// El delta de carga es el único que trae su detalle dentro del payload, y el
/// único que puede **destruir** inventario que el vendedor ya usó. Dos escenarios
/// lo rompen en silencio y ninguno se nota probando el camino feliz:
///
/// 1. **El `pull` se repite tras un corte de red.** Si reaplicar el delta
///    volviera a poner `cant_actual = cant_cargada`, el camión recuperaría en la
///    base la mercancía que ya salió físicamente. El vendedor la volvería a
///    vender, y el faltante aparecería en la liquidación sin explicación.
///
/// 2. **La oficina liquida la carga de ayer a media mañana.** Ese delta llega
///    DESPUÉS de la carga de hoy. Si se tratara como "éste es el inventario
///    vigente", borraría el de hoy y repondría el de ayer — con el camión ya en
///    la calle.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _ayer = 'carga-de-ayer';
const _hoy = 'carga-de-hoy';
const _atun = 'p-atun';
const _sopa = 'p-sopa';

Delta _cargaDelta(
  String cargaId, {
  String estado = 'confirmada',
  List<Map<String, Object?>> detalle = const [],
  String fecha = '2026-09-29',
  String operacion = 'upsert',
}) =>
    Delta(
      cursor: 1,
      entidad: 'carga',
      entidadId: cargaId,
      operacion: operacion,
      payload: {
        'id': cargaId,
        'folio': 'CG-000001',
        'estado': estado,
        'version': 1,
        'fecha_operativa': fecha,
        'detalle': detalle,
      },
    );

Map<String, Object?> _renglon(String producto, String cantidad) => {
      'producto_id': producto,
      'cantidad': cantidad,
      'lote': null,
      'caducidad': null,
    };

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    aplicador = AplicadorDeltas(db);
  });

  tearDown(() => db.dispose());

  double? cantidadActual(String producto) {
    final filas = db.select(
      'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
      [producto],
    );
    return filas.isEmpty ? null : filas.single['cant_actual'] as double;
  }

  String? cargaActiva() {
    final filas = db.select(
      "SELECT valor FROM sync_estado WHERE clave = 'carga_id_activa'",
    );
    return filas.isEmpty ? null : filas.single['valor'] as String?;
  }

  void aplicar(Delta d) => aplicador.aplicar([d], recibidoEn: '2026-09-29T12:00:00.000Z');

  // -------------------------------------------------------------------------

  test('la carga confirmada llena el camión con su detalle', () {
    aplicar(_cargaDelta(_hoy, detalle: [
      _renglon(_atun, '240.000'),
      _renglon(_sopa, '48.000'),
    ]));

    final filas = db.select(
      'SELECT * FROM existencias_camion ORDER BY producto_id',
    );
    expect(filas.length, equals(2));
    expect(filas.first['producto_id'], equals(_atun));
    expect(filas.first['cant_cargada'], equals(240.0));
    expect(filas.first['cant_actual'], equals(240.0));
    expect(filas.first['carga_id'], equals(_hoy));
  });

  test('la cantidad llega como string de tres decimales y no la toca ningún double', () {
    // '240.000' → Cantidad (milésimas enteras) → 240.0. Si el string se parseara
    // con `double.parse` directo funcionaría por casualidad con 240; con 0.001
    // por unidad y miles de renglones, no.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '12.500')]));
    expect(cantidadActual(_atun), equals(12.5));
  });

  test('fija la carga activa y su fecha operativa', () {
    // Sin la carga activa, la venta sale sin `carga_id` y la liquidación no tiene
    // contra qué cuadrar el día.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')], fecha: '2026-09-29'));

    expect(cargaActiva(), equals(_hoy));
    expect(
      db
          .select("SELECT valor FROM sync_estado WHERE clave = 'fecha_operativa'")
          .single['valor'],
      equals('2026-09-29'),
    );
  });

  test('REAPLICAR EL MISMO DELTA NO REVIVE LO VENDIDO', () {
    // El caso que un `pull` repetido provoca de verdad.
    final delta = _cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]);
    aplicar(delta);

    // El vendedor vendió 90 piezas en la calle.
    db.execute('UPDATE existencias_camion SET cant_actual = 150 WHERE producto_id = ?',
        [_atun]);

    aplicar(delta);

    expect(
      cantidadActual(_atun),
      equals(150.0),
      reason: 'reaplicar la carga repuso mercancía que ya salió del camión',
    );
    // Y el snapshot de lo cargado sigue siendo el original.
    expect(
      db.select('SELECT cant_cargada FROM existencias_camion').single['cant_cargada'],
      equals(240.0),
    );
  });

  test('una carga nueva se lleva el sobrante de la anterior', () {
    aplicar(_cargaDelta(_ayer, detalle: [_renglon(_sopa, '48.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 6');

    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));

    // La carga confirmada es el inventario COMPLETO del día: lo que no viene en
    // ella no está en el camión. Dejar las 6 sopas de ayer haría que el vendedor
    // las ofreciera sin traerlas.
    expect(cantidadActual(_sopa), isNull);
    expect(cantidadActual(_atun), equals(240.0));
    expect(cargaActiva(), equals(_hoy));
  });

  test('LIQUIDAR LA CARGA DE AYER NO BORRA LA DE HOY', () {
    // La oficina liquida lo de ayer a media mañana, con el camión ya en la calle.
    // Ese delta llega DESPUÉS del de hoy.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 150');

    aplicar(_cargaDelta(_ayer, estado: 'liquidada', detalle: [_renglon(_sopa, '48.000')]));

    expect(
      cantidadActual(_atun),
      equals(150.0),
      reason: 'el cierre de una carga vieja se llevó el inventario del día',
    );
    expect(cargaActiva(), equals(_hoy), reason: 'perdió la carga activa del día');
  });

  test('cancelar la carga vigente vacía el camión y suelta la carga activa', () {
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    aplicar(_cargaDelta(_hoy, estado: 'cancelada'));

    expect(db.select('SELECT * FROM existencias_camion'), isEmpty);
    // Una venta sin carga es mejor que una venta amarrada a una carga cancelada.
    expect(cargaActiva(), isNull);
  });

  test('un borrador no llena el camión', () {
    // El servidor no los publica (migración 0015). Esta prueba es la segunda
    // línea: si algún día los publicara, el vendedor NO debe ver mercancía que la
    // bodega todavía no le entregó.
    aplicar(_cargaDelta(_hoy, estado: 'borrador', detalle: [_renglon(_atun, '240.000')]));

    expect(db.select('SELECT * FROM existencias_camion'), isEmpty);
    expect(cargaActiva(), isNull);
  });

  test('una carga confirmada SIN renglones no vacía el camión', () {
    // El panel no deja confirmar una carga vacía, así que esto solo llega de un
    // script contra la base: alguien inserta la carga ya en 'confirmada' y le
    // pone el detalle después. Ese delta sale con `detalle: []`, y tratarlo como
    // el inventario del día le dejaría el camión vacío al vendedor a media ruta.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 150');

    aplicar(_cargaDelta('carga-vacia', detalle: const []));

    expect(cantidadActual(_atun), equals(150.0));
    expect(cargaActiva(), equals(_hoy));
  });

  test('un delta de borrado se lleva solo lo de esa carga', () {
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    aplicar(_cargaDelta(_ayer, operacion: 'delete'));

    expect(cantidadActual(_atun), equals(240.0));
    expect(cargaActiva(), equals(_hoy));
  });

  test('un producto que el teléfono todavía no conoce NO tumba la transacción', () {
    // `existencias_camion` no tiene llave foránea a productos, y esta prueba es la
    // razón: los deltas se aplican todo-o-nada, así que una llave foránea
    // insatisfecha abortaría la tanda entera y el dispositivo NO VOLVERÍA A
    // SINCRONIZAR NUNCA. El renglón huérfano simplemente no aparece en el
    // catálogo hasta que llegue su producto.
    expect(
      () => aplicar(_cargaDelta(_hoy, detalle: [_renglon('producto-que-no-existe', '10.000')])),
      returnsNormally,
    );
    expect(cantidadActual('producto-que-no-existe'), equals(10.0));
  });

  test('la carga ya no cae en deltas_desconocidos', () {
    // Se aceptaba y se tiraba: el teléfono sabía que le habían cargado el camión
    // y no qué.
    final r = aplicador.aplicar(
      [_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')])],
      recibidoEn: '2026-09-29T12:00:00.000Z',
    );
    expect(r.aplicados, equals(1));
    expect(r.desconocidos, equals(0));
    expect(db.select('SELECT * FROM deltas_desconocidos'), isEmpty);
  });
}
