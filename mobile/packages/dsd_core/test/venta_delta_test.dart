/// La venta que la oficina cambió, aplicada en el teléfono.
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ───────────────────────────────────────────────────────────────────────────
/// Cuando gerencia cancela o corrige una venta en el panel, el vendedor tiene que
/// ver lo mismo en su teléfono: la mercancía de vuelta en el camión y la venta con
/// su nuevo importe. Si no, el camión arrastra un faltante que el vendedor paga en
/// la liquidación por algo que la oficina decidió.
///
/// El delta trae las partidas como quedaron, no la diferencia, y el teléfono
/// devuelve al camión lo que sobra comparando contra lo que él tiene. Eso lo hace
/// idempotente por construcción —aplicarlo dos veces da cero la segunda—, y es lo
/// que estas pruebas comprueban más veces.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _venta = 'venta-de-hoy';
const _atun = 'p-atun';
const _sopa = 'p-sopa';

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    aplicador = AplicadorDeltas(db);

    db.execute(
      "INSERT INTO clientes (id, nombre_comercial) VALUES ('cli-1', 'La Esquina')",
    );
    for (final p in [_atun, _sopa]) {
      db.execute(
        'INSERT INTO productos (id, sku, nombre, unidad_base) VALUES (?, ?, ?, ?)',
        [p, p.toUpperCase(), p, 'PZA'],
      );
      db.execute(
        'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual) '
        'VALUES (?, 600, 120)',
        [p],
      );
    }
  });

  tearDown(() => db.dispose());

  /// La venta como la dejó el teléfono: 20 cajas de atún (480 piezas).
  void sembrarVenta() {
    db.execute(
      "INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo, "
      "       estado, subtotal, total, fecha_dispositivo, fecha_operativa, creado_en) "
      "VALUES (?, 1, 'VEND01-000001', 'cli-1', 'contado', 'confirmada', 6000, 6000, "
      "        '2026-10-05T10:00:00Z', '2026-10-05', '2026-10-05T10:00:00Z')",
      [_venta],
    );
    db.execute(
      'INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo, '
      '       factor_unidad, cantidad, cantidad_base, precio_unitario, importe) '
      "VALUES ('pa-1', ?, 1, ?, 'CAJA', 24, 20, 480, 300, 6000)",
      [_venta, _atun],
    );
  }

  Delta delta({
    String estado = 'confirmada',
    List<Map<String, Object?>> partidas = const [],
    String total = '6000.00',
    String? cancelacion,
    String? correccion,
  }) =>
      Delta(
        cursor: 1,
        entidad: 'venta',
        entidadId: _venta,
        operacion: 'upsert',
        payload: {
          'id': _venta,
          'estado': estado,
          'subtotal': total,
          'descuento': '0.00',
          'impuestos': '0.00',
          'total': total,
          'partidas': partidas,
          'cancelacion_motivo': cancelacion,
          'correccion_motivo': correccion,
        },
      );

  Map<String, Object?> partida(String producto, String cantidad, String base) => {
        'id': 'pa-1',
        'linea': 1,
        'producto_id': producto,
        'unidad_codigo': 'CAJA',
        'factor_unidad': '24.0000',
        'cantidad': cantidad,
        'cantidad_base': base,
        'precio_unitario': '300.0000',
        'tasa_iva': '0.0000',
        'importe': '600.00',
      };

  void aplicar(Delta d) =>
      aplicador.aplicar([d], recibidoEn: '2026-10-05T18:00:00.000Z');

  double enCamion(String producto) => db.select(
        'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
        [producto],
      ).single['cant_actual'] as double;

  Row venta() => db.select('SELECT * FROM ventas WHERE id = ?', [_venta]).single;

  // -------------------------------------------------------------------------

  test('CANCELAR DEVUELVE TODA LA MERCANCÍA AL CAMIÓN', () {
    sembrarVenta();
    aplicar(delta(estado: 'cancelada', total: '0.00', cancelacion: 'cliente equivocado'));

    expect(enCamion(_atun), equals(600.0));
    expect(venta()['estado'], equals('cancelada'));
    expect(venta()['nota_oficina'], equals('cliente equivocado'));
    // Y la venta se queda sin partidas: ya no entrega nada.
    expect(db.select('SELECT * FROM venta_partidas'), isEmpty);
  });

  test('CORREGIR DEVUELVE SOLO LA DIFERENCIA', () {
    sembrarVenta();
    // 20 cajas → 2 cajas: vuelven 18 cajas = 432 piezas.
    aplicar(delta(
      partidas: [partida(_atun, '2.000', '48.000')],
      total: '600.00',
      correccion: 'eran 2 cajas, no 20',
    ));

    expect(enCamion(_atun), equals(552.0));
    expect(venta()['total'], equals(600.0));
    expect(venta()['nota_oficina'], equals('eran 2 cajas, no 20'));

    final p = db.select('SELECT * FROM venta_partidas').single;
    expect(p['cantidad'], equals(2.0));
    expect(p['cantidad_base'], equals(48.0));
  });

  test('APLICARLO DOS VECES NO DEVUELVE LA MERCANCÍA DOS VECES', () {
    // El caso que un `pull` repetido provoca de verdad. Si el delta trajera la
    // diferencia en vez de las partidas, la segunda vez le regalaría al camión 432
    // piezas que no existen.
    sembrarVenta();
    final d = delta(
      partidas: [partida(_atun, '2.000', '48.000')],
      total: '600.00',
      correccion: 'eran 2 cajas',
    );
    aplicar(d);
    aplicar(d);

    expect(enCamion(_atun), equals(552.0));
  });

  test('cancelar y luego recibir el delta otra vez tampoco', () {
    sembrarVenta();
    final d = delta(estado: 'cancelada', total: '0.00', cancelacion: 'duplicada');
    aplicar(d);
    aplicar(d);

    expect(enCamion(_atun), equals(600.0));
  });

  test('una venta que ESTE teléfono no tiene se ignora', () {
    // Pasa después de reinstalar la app: el servidor tiene la venta y el teléfono
    // no. Tocar el camión sería adivinar — el saldo que este teléfono trae ya
    // viene del servidor, con esa venta dentro.
    aplicar(delta(estado: 'cancelada', total: '0.00', cancelacion: 'vieja'));

    expect(enCamion(_atun), equals(120.0));
    expect(db.select('SELECT * FROM ventas'), isEmpty);
  });

  test('un renglón que desaparece del documento devuelve lo suyo', () {
    sembrarVenta();
    // Una segunda partida de sopa que la oficina quita entera.
    db.execute(
      'INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo, '
      '       factor_unidad, cantidad, cantidad_base, precio_unitario, importe) '
      "VALUES ('pa-2', ?, 2, ?, 'PZA', 1, 30, 30, 12, 360)",
      [_venta, _sopa],
    );

    aplicar(delta(
      partidas: [partida(_atun, '20.000', '480.000')],
      total: '6000.00',
      correccion: 'la sopa no se entregó',
    ));

    expect(enCamion(_sopa), equals(150.0), reason: 'las 30 sopas no volvieron');
    expect(enCamion(_atun), equals(120.0), reason: 'el atún no cambió');
    expect(db.select('SELECT * FROM venta_partidas').length, equals(1));
  });

  test('un producto que el camión no trae no se crea aquí', () {
    // Si el camión no lo trae es porque la carga no lo subió. Un renglón nuevo le
    // mostraría al vendedor mercancía que no tiene.
    db.execute('DELETE FROM existencias_camion WHERE producto_id = ?', [_atun]);
    sembrarVenta();

    aplicar(delta(estado: 'cancelada', total: '0.00', cancelacion: 'x'));

    expect(
      db.select('SELECT * FROM existencias_camion WHERE producto_id = ?', [_atun]),
      isEmpty,
    );
    expect(venta()['estado'], equals('cancelada'));
  });

  test('la venta no cae en deltas_desconocidos', () {
    sembrarVenta();
    final r = aplicador.aplicar(
      [delta(estado: 'cancelada', total: '0.00')],
      recibidoEn: '2026-10-05T18:00:00.000Z',
    );
    expect(r.aplicados, equals(1));
    expect(r.desconocidos, equals(0));
  });
}
