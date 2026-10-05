/// El ajuste que la oficina hizo al camión, aplicado en el teléfono.
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ───────────────────────────────────────────────────────────────────────────
/// Cuando gerencia corrige el inventario de un camión en el panel, el vendedor
/// tiene que ver el mismo número. Si no, el catálogo le ofrece a los clientes
/// mercancía que no trae — y el descuadre acaba en su liquidación.
///
/// El ajuste viaja como una DIFERENCIA firmada y no como el saldo resultante: el
/// vendedor puede estar vendiendo mientras la oficina corrige, y un saldo de hace
/// cinco minutos aplicado ahora borraría las ventas de esos cinco minutos. El
/// precio es que sumarlo dos veces está mal, y de eso se encarga la tabla de
/// ajustes ya aplicados.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _atun = 'p-atun';

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    aplicador = AplicadorDeltas(db);
    db.execute(
      "INSERT INTO productos (id, sku, nombre, unidad_base) "
      "VALUES (?, 'ATUN-140', 'Atún', 'PZA')",
      [_atun],
    );
    db.execute(
      'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual) '
      'VALUES (?, 600, 120)',
      [_atun],
    );
  });

  tearDown(() => db.dispose());

  Delta ajuste(
    String id,
    String delta, {
    String tipo = 'conteo',
    String producto = _atun,
    String nota = 'Juan reportó que trae 12 cajas, no 30',
  }) =>
      Delta(
        cursor: 1,
        entidad: 'ajuste_camion',
        entidadId: id,
        operacion: 'upsert',
        payload: {
          'id': id,
          'folio': 'AC-000001',
          'producto_id': producto,
          'delta': delta,
          'tipo': tipo,
          'nota': nota,
        },
      );

  void aplicar(Delta d) =>
      aplicador.aplicar([d], recibidoEn: '2026-10-05T18:00:00.000Z');

  double enCamion(String producto) => db.select(
        'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
        [producto],
      ).single['cant_actual'] as double;

  // -------------------------------------------------------------------------

  test('un ajuste que BAJA el saldo lo baja', () {
    aplicar(ajuste('aj-1', '-18.000'));
    expect(enCamion(_atun), equals(102.0));
  });

  test('un ajuste que SUBE el saldo lo sube', () {
    aplicar(ajuste('aj-2', '24.000', tipo: 'entrada'));
    expect(enCamion(_atun), equals(144.0));
  });

  test('EL MISMO AJUSTE NO SE APLICA DOS VECES', () {
    // El caso que un `pull` repetido provoca de verdad. Sin la marca, el vendedor
    // perdería 36 piezas por un ajuste de 18.
    final d = ajuste('aj-3', '-18.000');
    aplicar(d);
    aplicar(d);
    expect(enCamion(_atun), equals(102.0));
  });

  test('dos ajustes DISTINTOS se suman los dos', () {
    // La marca es por ajuste, no por producto: dos correcciones del mismo
    // producto son dos hechos.
    aplicar(ajuste('aj-4', '-18.000'));
    aplicar(ajuste('aj-5', '-2.000'));
    expect(enCamion(_atun), equals(100.0));
  });

  test('el ajuste no revive un producto que el camión no trae', () {
    aplicar(ajuste('aj-6', '10.000', producto: 'producto-que-no-subio'));

    expect(
      db.select(
        'SELECT * FROM existencias_camion WHERE producto_id = ?',
        ['producto-que-no-subio'],
      ),
      isEmpty,
    );
    // Pero queda marcado: si el delta llega otra vez, tampoco hace nada.
    expect(
      db.select('SELECT * FROM ajustes_camion_aplicados').length,
      equals(1),
    );
  });

  test('se guarda el folio y la nota, para poder decirle al vendedor qué pasó', () {
    aplicar(ajuste('aj-7', '-18.000', nota: 'se contó con él por teléfono'));

    final fila = db.select('SELECT * FROM ajustes_camion_aplicados').single;
    expect(fila['folio'], equals('AC-000001'));
    expect(fila['nota'], equals('se contó con él por teléfono'));
  });

  test('el ajuste no cae en deltas_desconocidos', () {
    final r = aplicador.aplicar(
      [ajuste('aj-8', '-1.000')],
      recibidoEn: '2026-10-05T18:00:00.000Z',
    );
    expect(r.aplicados, equals(1));
    expect(r.desconocidos, equals(0));
  });

  test('un ajuste puede dejar el camión en negativo', () {
    // La pantalla del panel no deja CAPTURAR un ajuste que empuje a negativo, pero
    // el teléfono no es quien valida eso: si el servidor lo mandó, el saldo del
    // servidor ya es ése, y discrepar sería peor que quedar negativo. §0.1.
    aplicar(ajuste('aj-9', '-200.000'));
    expect(enCamion(_atun), equals(-80.0));
  });
}
