/// El corte del día del vendedor.
///
/// La cuenta que importa es `efectivo = contado + cobros en efectivo`, y tiene
/// que dar LO MISMO que `_efectivo_esperado` del servidor, que es con la que la
/// oficina le cobra en la liquidación. Si las dos no coinciden, el vendedor llega
/// a la bodega con un número distinto del que le van a pedir.
library;

import 'dart:io';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _hoy = '2026-10-05';
const _ayer = '2026-10-04';

void main() {
  late Database db;
  late RepoMiDia repo;

  setUp(() {
    db = sqlite3.openInMemory();
    db.execute(File('../../db/schema.sql').readAsStringSync());
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial) VALUES ('c1', 'Doña Mary')",
    );
    repo = RepoMiDia(db);
  });

  tearDown(() => db.dispose());

  void venta({
    required String folio,
    required double total,
    String tipo = 'contado',
    String fecha = _hoy,
    String estado = 'confirmada',
    int sincronizada = 0,
    String cliente = 'c1',
  }) =>
      db.execute(
        'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo, '
        'estado, total, fecha_dispositivo, fecha_operativa, sincronizada, creado_en) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [folio, folio.hashCode.abs(), folio, cliente, tipo, estado, total,
         '${fecha}T10:00:00Z', fecha, sincronizada, '${fecha}T10:00:00Z'],
      );

  void cobro({
    required String folio,
    required double importe,
    String forma = 'efectivo',
    String fecha = _hoy,
    String estado = 'confirmado',
  }) =>
      db.execute(
        'INSERT INTO cobros (id, folio_consecutivo, folio_local, cliente_id, '
        'importe, forma_pago, estado, fecha_dispositivo, fecha_operativa, creado_en) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [folio, folio.hashCode.abs(), folio, 'c1', importe, forma, estado,
         '${fecha}T11:00:00Z', fecha, '${fecha}T11:00:00Z'],
      );

  test('el efectivo son las ventas de contado más los cobros en efectivo', () {
    venta(folio: 'V1', total: 100.00);
    venta(folio: 'V2', total: 50.00);
    cobro(folio: 'C1', importe: 25.00);

    final dia = repo.delDia(_hoy);

    expect(dia.contado, equals(Dinero.deTexto('150.00')));
    expect(dia.cobrosEfectivo, equals(Dinero.deTexto('25.00')));
    expect(dia.efectivo, equals(Dinero.deTexto('175.00')));
  });

  test('una venta a CRÉDITO no es efectivo', () {
    // Es la regla que más cuesta: salió mercancía y no entró dinero. Sumarla haría
    // que el vendedor creyera que trae un dinero que no tiene.
    venta(folio: 'V1', total: 100.00);
    venta(folio: 'V2', total: 400.00, tipo: 'credito');

    final dia = repo.delDia(_hoy);

    expect(dia.contado, equals(Dinero.deTexto('100.00')));
    expect(dia.credito, equals(Dinero.deTexto('400.00')));
    expect(dia.efectivo, equals(Dinero.deTexto('100.00')), reason: 'el crédito no entra a la bolsa');
    expect(dia.vendido, equals(Dinero.deTexto('500.00')), reason: 'pero sí es mercancía que salió');
  });

  test('una transferencia entra al sistema y no a la bolsa', () {
    cobro(folio: 'C1', importe: 300.00, forma: 'transferencia');
    cobro(folio: 'C2', importe: 70.00);

    final dia = repo.delDia(_hoy);

    expect(dia.cobrosEfectivo, equals(Dinero.deTexto('70.00')));
    expect(dia.cobrosOtros, equals(Dinero.deTexto('300.00')));
    expect(dia.efectivo, equals(Dinero.deTexto('70.00')));
  });

  test('lo de ayer no cuenta hoy', () {
    // El arqueo es de UN día. Arrastrar el anterior haría que el vendedor
    // entregara dos veces el mismo dinero, o que pareciera que falta.
    venta(folio: 'V-AYER', total: 999.00, fecha: _ayer);
    cobro(folio: 'C-AYER', importe: 500.00, fecha: _ayer);
    venta(folio: 'V-HOY', total: 10.00);

    final dia = repo.delDia(_hoy);

    expect(dia.efectivo, equals(Dinero.deTexto('10.00')));
    expect(dia.ventas.length, equals(1));
  });

  test('una venta cancelada no cuenta', () {
    venta(folio: 'V1', total: 100.00);
    venta(folio: 'V2', total: 60.00, estado: 'cancelada');

    expect(repo.delDia(_hoy).efectivo, equals(Dinero.deTexto('100.00')));
  });

  test('se cuenta lo que todavía no sale del teléfono', () {
    // Es lo que el vendedor necesita saber ANTES de llegar a la bodega: una venta
    // sin sincronizar es dinero que la oficina todavía no sabe que existe, y si
    // llega con el teléfono en cero de batería ese documento se discute.
    venta(folio: 'V1', total: 100.00, sincronizada: 1);
    venta(folio: 'V2', total: 50.00);
    cobro(folio: 'C1', importe: 25.00);

    final dia = repo.delDia(_hoy);

    expect(dia.sinSincronizar, equals(2), reason: 'la venta V2 y el cobro C1');
    expect(dia.ventas.firstWhere((v) => v.folio == 'V1').sincronizada, isTrue);
  });

  test('un día sin nada se reconoce como vacío', () {
    expect(repo.delDia(_hoy).vacio, isTrue);
    venta(folio: 'V1', total: 1.00);
    expect(repo.delDia(_hoy).vacio, isFalse);
  });

  test('la venta trae el nombre del cliente', () {
    // Un folio sin nombre no sirve: el vendedor reconoce su día por las tiendas,
    // no por los consecutivos.
    //
    // El `LEFT JOIN` con `COALESCE` se queda aunque la clave foránea de
    // `ventas.cliente_id` ya garantice que el cliente existe —lo comprobé: SQLite
    // rechaza la venta de un cliente inexistente—. Es seguro de bajo costo, no una
    // rama que haya que probar: una prueba de ese caso sería imposible de escribir,
    // y una prueba imposible es la señal de que el caso no existe.
    venta(folio: 'V1', total: 10.00);

    expect(repo.delDia(_hoy).ventas.single.cliente, equals('Doña Mary'));
  });

  group('lo que la oficina tocó', () {
    test('UNA VENTA CANCELADA DEJA DE SUMAR AL EFECTIVO', () {
      // Es lo mismo que hace el arqueo del servidor. Si siguiera sumando, el
      // vendedor llegaría a la bodega esperando entregar un dinero que la oficina
      // ya no le va a pedir.
      venta(folio: 'A-1', total: 500);
      venta(folio: 'A-2', total: 300, estado: 'cancelada');

      final dia = repo.delDia(_hoy);
      expect(dia.efectivo, equals(Dinero.deTexto('500.00')));
      expect(dia.ventas.length, equals(1));
    });

    test('pero APARECE, con su motivo', () {
      // Si solo bajara el total, el vendedor vería su número caer sin explicación
      // y pensaría que la app le perdió una venta.
      venta(folio: 'A-2', total: 300, estado: 'cancelada');
      db.execute(
        "UPDATE ventas SET nota_oficina = 'se facturó al cliente equivocado' "
        "WHERE folio_local = 'A-2'",
      );

      final tocadas = repo.delDia(_hoy).tocadasPorOficina;
      expect(tocadas.length, equals(1));
      expect(tocadas.single.folio, equals('A-2'));
      expect(tocadas.single.cancelada, isTrue);
      expect(tocadas.single.nota, equals('se facturó al cliente equivocado'));
    });

    test('una corregida sigue contando, con su importe nuevo', () {
      venta(folio: 'A-3', total: 600);
      db.execute(
        "UPDATE ventas SET nota_oficina = 'eran 2 cajas, no 20' "
        "WHERE folio_local = 'A-3'",
      );

      final dia = repo.delDia(_hoy);
      expect(dia.efectivo, equals(Dinero.deTexto('600.00')));
      expect(dia.tocadasPorOficina.single.cancelada, isFalse);
      expect(dia.tocadasPorOficina.single.nota, equals('eran 2 cajas, no 20'));
    });

    test('un día limpio no trae ninguna', () {
      venta(folio: 'A-1', total: 500);
      expect(repo.delDia(_hoy).tocadasPorOficina, isEmpty);
    });

    test('lo que la oficina tocó AYER no aparece hoy', () {
      venta(folio: 'A-9', total: 300, estado: 'cancelada', fecha: _ayer);
      expect(repo.delDia(_hoy).tocadasPorOficina, isEmpty);
    });
  });
}
