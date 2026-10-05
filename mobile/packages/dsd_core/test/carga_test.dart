/// La carga del camión, del lado del teléfono.
///
/// ───────────────────────────────────────────────────────────────────────────
/// EL CAMIÓN ES UN ALMACÉN RODANTE
/// ───────────────────────────────────────────────────────────────────────────
/// Decisión de la dirección, octubre 2026: la mercancía que no se vende se queda
/// a dormir en el camión y se acumula con la carga del día siguiente. Así que la
/// carga **se suma** al saldo; no lo reemplaza.
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ───────────────────────────────────────────────────────────────────────────
/// El delta de carga es el único que trae su detalle dentro del payload, y el
/// único que puede **destruir** inventario que el vendedor ya usó. Cuatro
/// escenarios lo rompen en silencio y ninguno se nota probando el camino feliz:
///
/// 1. **El `pull` se repite tras un corte de red.** El mismo delta llega dos
///    veces. Sumarlo dos veces le regalaría al camión una carga completa: el
///    vendedor la ofrecería, no la tendría, y el descuadre saldría en la
///    liquidación sin explicación. Reemplazar era idempotente por naturaleza;
///    sumar exige recordar qué cargas ya entraron.
///
/// 2. **La carga de hoy no puede borrar el sobrante de ayer.** Es la decisión de
///    negocio: lo de antes sigue arriba del camión.
///
/// 3. **La oficina liquida la carga de ayer a media mañana.** Ese delta llega
///    DESPUÉS de la carga de hoy y con ventas ya hechas. Por eso trae el AJUSTE
///    del conteo —una diferencia con signo— y no el conteo: un conteo de ayer
///    aplicado como "el camión tiene esto" borraría la carga de hoy.
///
/// 4. **El ajuste del cierre tampoco se aplica dos veces**, o el faltante se le
///    cobraría al vendedor por duplicado.
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
  List<Map<String, Object?>>? ajustes,
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
        if (ajustes != null) 'ajustes': ajustes,
      },
    );

/// Un renglón de ajuste del cierre: la diferencia CON SIGNO.
Map<String, Object?> _ajuste(String producto, String cantidad) => {
      'producto_id': producto,
      'cantidad': cantidad,
    };

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

  test('REAPLICAR EL MISMO DELTA NO SUMA LA CARGA DOS VECES', () {
    // El caso que un `pull` repetido provoca de verdad. Con la carga sumándose en
    // vez de reemplazando, esto es lo único que separa al vendedor de ver el
    // doble de mercancía de la que trae.
    final delta = _cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]);
    aplicar(delta);

    // El vendedor vendió 90 piezas en la calle.
    db.execute('UPDATE existencias_camion SET cant_actual = 150 WHERE producto_id = ?',
        [_atun]);

    aplicar(delta);

    expect(
      cantidadActual(_atun),
      equals(150.0),
      reason: 'reaplicar la carga volvió a sumarla, o repuso lo ya vendido',
    );
    // Y el snapshot de lo cargado sigue siendo el de esa carga.
    expect(
      db.select('SELECT cant_cargada FROM existencias_camion').single['cant_cargada'],
      equals(240.0),
    );
  });

  test('UNA CARGA NUEVA SE SUMA AL SOBRANTE DE LA ANTERIOR', () {
    // La decisión de negocio, probada: el camión no amanece en ceros.
    //
    // Antes esta prueba afirmaba lo contrario —"una carga nueva se lleva el
    // sobrante de la anterior"— porque el modelo era que la carga confirmada era
    // el inventario completo del día. Con mercancía durmiendo arriba del camión,
    // eso le borraba al vendedor lo que traía y el cierre se lo cobraba.
    aplicar(_cargaDelta(_ayer, detalle: [_renglon(_sopa, '48.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 6');

    aplicar(_cargaDelta(_hoy, detalle: [
      _renglon(_atun, '240.000'),
      _renglon(_sopa, '24.000'),
    ]));

    // Las 6 sopas que quedaron ayer siguen arriba, más las 24 de hoy.
    expect(cantidadActual(_sopa), equals(30.0));
    expect(cantidadActual(_atun), equals(240.0));
    expect(cargaActiva(), equals(_hoy));
  });

  test('un producto que hoy no se cargó se queda con su saldo', () {
    aplicar(_cargaDelta(_ayer, detalle: [_renglon(_sopa, '48.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 6');

    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));

    expect(
      cantidadActual(_sopa),
      equals(6.0),
      reason: 'el vendedor trae esas 6 sopas y tiene que poder venderlas',
    );
  });

  test('LIQUIDAR LA CARGA DE AYER NO BORRA LA DE HOY', () {
    // La oficina liquida lo de ayer a media mañana, con el camión ya en la calle.
    // Ese delta llega DESPUÉS del de hoy.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 150');

    aplicar(_cargaDelta(_ayer,
        estado: 'liquidada', detalle: [_renglon(_sopa, '48.000')], ajustes: []));

    expect(
      cantidadActual(_atun),
      equals(150.0),
      reason: 'el cierre de una carga vieja se llevó el inventario del día',
    );
    expect(cargaActiva(), equals(_hoy), reason: 'perdió la carga activa del día');
  });

  test('EL CIERRE NO VACÍA EL CAMIÓN: la mercancía se queda arriba', () {
    // Lo que antes hacía este delta era borrar `existencias_camion`. El vendedor
    // amanecía en ceros con el camión lleno.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 60');

    aplicar(_cargaDelta(_hoy, estado: 'liquidada', ajustes: []));

    expect(cantidadActual(_atun), equals(60.0));
    // Pero la carga ya terminó: deja de ser la activa.
    expect(cargaActiva(), isNull);
  });

  test('el ajuste del cierre se SUMA al saldo, con su signo', () {
    // El conteo dijo 54 donde el sistema tenía 60: faltan 6, y la oficina las
    // sacó del camión. El teléfono tiene que quedar en el mismo número que el
    // servidor, o mañana los dos discuten.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 60');

    aplicar(_cargaDelta(_hoy,
        estado: 'liquidada', ajustes: [_ajuste(_atun, '-6.000')]));

    expect(cantidadActual(_atun), equals(54.0));
  });

  test('un sobrante del cierre entra al camión', () {
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 60');

    aplicar(_cargaDelta(_hoy,
        estado: 'liquidada', ajustes: [_ajuste(_atun, '6.000')]));

    expect(cantidadActual(_atun), equals(66.0));
  });

  test('EL AJUSTE DEL CIERRE NO SE APLICA DOS VECES', () {
    // El delta de la carga liquidada se puede repetir igual que cualquier otro.
    // Aplicar el faltante dos veces se lo cobraría al vendedor por duplicado.
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    db.execute('UPDATE existencias_camion SET cant_actual = 60');

    final cierre = _cargaDelta(_hoy,
        estado: 'liquidada', ajustes: [_ajuste(_atun, '-6.000')]);
    aplicar(cierre);
    aplicar(cierre);

    expect(cantidadActual(_atun), equals(54.0));
  });

  test('cancelar la carga vigente RESTA lo que esa carga había subido', () {
    // Y no borra el renglón: lo que el camión traía de antes sigue arriba.
    aplicar(_cargaDelta(_ayer, detalle: [_renglon(_atun, '40.000')]));
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));
    expect(cantidadActual(_atun), equals(280.0));

    aplicar(_cargaDelta(_hoy,
        estado: 'cancelada', detalle: [_renglon(_atun, '240.000')]));

    expect(cantidadActual(_atun), equals(40.0));
    // Una venta sin carga es mejor que una venta amarrada a una carga cancelada.
    expect(cargaActiva(), isNull);
  });

  test('cancelar una carga que nunca se aplicó no mueve inventario', () {
    aplicar(_cargaDelta(_hoy, detalle: [_renglon(_atun, '240.000')]));

    aplicar(_cargaDelta(_ayer,
        estado: 'cancelada', detalle: [_renglon(_atun, '100.000')]));

    expect(cantidadActual(_atun), equals(240.0));
    expect(cargaActiva(), equals(_hoy));
  });

  test('un borrador no llena el camión', () {
    // El servidor no los publica (migración 0015). Esta prueba es la segunda
    // línea: si algún día los publicara, el vendedor NO debe ver mercancía que la
    // bodega todavía no le entregó.
    aplicar(_cargaDelta(_hoy, estado: 'borrador', detalle: [_renglon(_atun, '240.000')]));

    expect(db.select('SELECT * FROM existencias_camion'), isEmpty);
    expect(cargaActiva(), isNull);
  });

  test('una carga confirmada SIN renglones no se marca como aplicada', () {
    // El panel no deja confirmar una carga vacía, así que esto solo llega de un
    // script contra la base: alguien inserta la carga ya en 'confirmada' y le
    // pone el detalle después. Si se marcara como aplicada, el delta que llegara
    // después con los renglones completos no entraría NUNCA.
    aplicar(_cargaDelta('carga-vacia', detalle: const []));
    expect(db.select('SELECT * FROM existencias_camion'), isEmpty);

    aplicar(_cargaDelta('carga-vacia', detalle: [_renglon(_atun, '240.000')]));
    expect(cantidadActual(_atun), equals(240.0));
  });

  test('un delta de borrado no se lleva el inventario del camión', () {
    // El payload de un borrado viene vacío: no hay detalle que restar, y restar a
    // ciegas sería peor. Solo suelta la carga activa si era esa.
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
