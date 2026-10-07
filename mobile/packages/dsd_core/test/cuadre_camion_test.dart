/// El cuadre del camión: el teléfono escribe lo que el servidor dice que trae.
///
/// ───────────────────────────────────────────────────────────────────────────
/// EL CASO QUE LO ORIGINÓ
/// ───────────────────────────────────────────────────────────────────────────
/// Octubre de 2026, en operación: el teléfono decía 1 Maruchan y el panel 0. La
/// oficina sumó 5 desde el panel y el teléfono pasó a 6. El ajuste viaja como
/// diferencia —tiene que, por las ventas que ocurren mientras viaja— y una
/// diferencia arrastra cualquier error que ya hubiera.
///
/// El cuadre escribe un ESTADO, y un estado que llega tarde borra lo que pasó en
/// medio. Por eso la mitad de estas pruebas son de cuándo NO se aplica.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

import 'ayudas_sync.dart';

const _camion = 'cam-1';
const _maruchan = 'p-maruchan';
const _atun = 'p-atun';
const _frijol = 'p-frijol';

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = baseLocal();
    aplicador = AplicadorDeltas(db);
    for (final (id, sku) in [(_maruchan, 'MAR-64'), (_atun, 'ATUN-140'), (_frijol, 'FRI-1K')]) {
      db.execute(
        "INSERT INTO productos (id, sku, nombre, unidad_base) VALUES (?, ?, ?, 'PZA')",
        [id, sku, sku],
      );
    }
    db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('almacen_asignado', ?)",
      [_camion],
    );
    // El camión del teléfono: 6 Maruchan (el error), 12 atunes.
    db.execute(
      'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual) '
      'VALUES (?, 0, 6), (?, 24, 12)',
      [_maruchan, _atun],
    );
  });

  tearDown(() => db.dispose());

  FotoDelCamion foto({
    Map<String, String> existencias = const {_maruchan: '5.000'},
    int cursor = 40,
    int cuarentena = 0,
    String almacen = _camion,
  }) =>
      FotoDelCamion(
        almacenId: almacen,
        cursor: cursor,
        cuarentena: cuarentena,
        existencias: existencias,
      );

  double? saldo(String producto) {
    final f = db.select(
      'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
      [producto],
    );
    return f.isEmpty ? null : (f.single['cant_actual'] as num).toDouble();
  }

  test('el teléfono queda con lo del servidor: 6 Maruchan pasan a 5', () {
    final r = aplicador.cuadrarCamion(
      foto(existencias: {_maruchan: '5.000', _atun: '12.000'}),
      cursorLocal: 40,
    );

    expect(r.aplicado, isTrue);
    expect(r.corregidos, equals(1));
    expect(saldo(_maruchan), equals(5));
    expect(saldo(_atun), equals(12));
  });

  test('lo que el servidor no trae, en el teléfono queda en cero', () {
    // La foto solo trae renglones con saldo: el atún que no aparece es cero.
    aplicador.cuadrarCamion(foto(), cursorLocal: 40);
    expect(saldo(_atun), equals(0));
  });

  test('lo que el servidor trae y el teléfono no, se agrega', () {
    aplicador.cuadrarCamion(
      foto(existencias: {_maruchan: '5.000', _atun: '12.000', _frijol: '3.500'}),
      cursorLocal: 40,
    );
    expect(saldo(_frijol), equals(3.5));
  });

  test('un producto que el catálogo local todavía no conoce no se inventa', () {
    final r = aplicador.cuadrarCamion(
      foto(existencias: {_maruchan: '5.000', 'p-nuevo': '2.000'}),
      cursorLocal: 40,
    );
    expect(r.aplicado, isTrue);
    expect(saldo('p-nuevo'), isNull);
  });

  test('aplicarlo dos veces no cambia nada la segunda', () {
    aplicador.cuadrarCamion(foto(), cursorLocal: 40);
    final otra = aplicador.cuadrarCamion(foto(), cursorLocal: 40);
    expect(otra.aplicado, isTrue);
    expect(otra.corregidos, equals(0));
  });

  group('cuándo NO se aplica', () {
    test('con ventas por subir: la foto todavía no las trae', () {
      // Si se aplicara, la venta de hace un minuto se le «devolvería» al camión.
      encolarAlta(Outbox(db), db, 'c1');
      final r = aplicador.cuadrarCamion(foto(), cursorLocal: 40);
      expect(r.aplicado, isFalse);
      expect(saldo(_maruchan), equals(6));
    });

    test('con cambios por traer: la foto ya los trae y se sumarían dos veces', () {
      final r = aplicador.cuadrarCamion(foto(cursor: 41), cursorLocal: 40);
      expect(r.aplicado, isFalse);
      expect(saldo(_maruchan), equals(6));
    });

    test('con operaciones en cuarentena: esa mercancía ya se entregó en la calle', () {
      final r = aplicador.cuadrarCamion(foto(cuarentena: 1), cursorLocal: 40);
      expect(r.aplicado, isFalse);
      expect(saldo(_maruchan), equals(6));
    });

    test('si es otro camión', () {
      final r = aplicador.cuadrarCamion(foto(almacen: 'cam-2'), cursorLocal: 40);
      expect(r.aplicado, isFalse);
      expect(saldo(_maruchan), equals(6));
    });
  });

  group('dentro de la sincronización', () {
    test('al terminar con todo entregado y todo traído, el camión se cuadra', () async {
      final transporte = TransporteFalso([])
        ..fotoDelCamion = {
          'almacen_id': _camion,
          'cursor': 0,
          'cuarentena': 0,
          'existencias': [
            {'producto_id': _maruchan, 'cantidad': '5.000'},
            {'producto_id': _atun, 'cantidad': '12.000'},
          ],
        };

      final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(r.fin, equals(FinDeSync.completa));
      expect(r.productosCuadrados, equals(1));
      expect(saldo(_maruchan), equals(5));
      expect(transporte.llamadas.last, startsWith('/v1/sync/camion'));
    });

    test('con cola pendiente ni siquiera se pregunta', () async {
      encolarAlta(Outbox(db), db, 'c1');
      final transporte = TransporteFalso([const SeCaeLaRed()])
        ..fotoDelCamion = {'almacen_id': _camion, 'cursor': 0, 'cuarentena': 0, 'existencias': []};

      await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);

      expect(transporte.llamadas.where((l) => l.contains('/v1/sync/camion')), isEmpty);
      expect(saldo(_maruchan), equals(6));
    });

    test('un servidor que no conoce el cuadre no tumba la sincronización', () async {
      final transporte = TransporteFalso([]); // contesta 404 al cuadre
      final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 0);
      expect(r.fin, equals(FinDeSync.completa));
      expect(saldo(_maruchan), equals(6));
    });
  });
}
