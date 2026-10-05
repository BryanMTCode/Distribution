/// Lo que impide que el teléfono deje de sincronizar.
///
/// ───────────────────────────────────────────────────────────────────────────
/// LA FALLA QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA
/// ───────────────────────────────────────────────────────────────────────────
/// La tanda de deltas se aplica en una transacción todo-o-nada, y el cursor **solo
/// avanza después de aplicarla**. Las dos cosas son correctas por separado y juntas
/// esconden la peor falla posible de este sistema:
///
///   un delta que lanza una excepción ⇒ la tanda se deshace ⇒ el cursor no avanza
///   ⇒ la siguiente corrida trae la MISMA tanda ⇒ revienta en el mismo renglón
///   ⇒ **el teléfono deja de sincronizar para siempre, y nadie se entera.**
///
/// Nadie se entera porque desde afuera se ve igual que un día sin cambios: la
/// pantalla no dice nada, la cola de salida sigue vaciándose, y el catálogo
/// simplemente se queda congelado. Un vendedor podría pasar una semana vendiendo
/// con los precios de la semana pasada.
///
/// La auditoría de octubre de 2026 encontró un caso real que lo disparaba —un delta
/// de baja de cliente contra la llave foránea de `ventas`— y las dos defensas que
/// se probaron aquí: que ese delta ya no lanza, y que **si alguno lanzara, se
/// aparta solo**.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = sqlite3.openInMemory();
    // Igual que en el teléfono: `BaseLocal.abrir` las activa, y sin esto las
    // llaves foráneas no se verifican y la prueba no probaría nada.
    db.execute('PRAGMA foreign_keys = ON');
    aplicarEsquemaLocal(db);
    aplicador = AplicadorDeltas(db);
  });

  tearDown(() => db.dispose());

  void conCliente(String id, {String nombre = 'La Esquina'}) => db.execute(
        'INSERT INTO clientes (id, nombre_comercial) VALUES (?, ?)',
        [id, nombre],
      );

  void conVentaSinSincronizar(String venta, String cliente) => db.execute(
        'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, total, '
        '       fecha_dispositivo, fecha_operativa, sincronizada, creado_en) '
        "VALUES (?, 1, ?, ?, 100, 'x', '2026-10-05', 0, 'x')",
        [venta, 'VEND01-00000$venta', cliente],
      );

  Delta bajaDeCliente(String id) => Delta(
        cursor: 1,
        entidad: 'cliente',
        entidadId: id,
        operacion: 'delete',
        payload: null,
      );

  Delta productoNuevo(String id) => Delta(
        cursor: 2,
        entidad: 'producto',
        entidadId: id,
        operacion: 'upsert',
        payload: {
          'id': id,
          'sku': id.toUpperCase(),
          'nombre': 'Producto $id',
          'unidad_base': 'PZA',
          'tasa_iva': 0,
          'activo': true,
        },
      );

  ResultadoAplicacion aplicar(List<Delta> deltas) =>
      aplicador.aplicar(deltas, recibidoEn: '2026-10-05T18:00:00.000Z');

  // -------------------------------------------------------------------------
  // Hallazgo 1: la baja de un cliente
  // -------------------------------------------------------------------------

  group('la baja de un cliente', () {
    test('NO BORRA: da de baja, y la venta sin subir sobrevive', () {
      // `ventas`, `cobros`, `no_drops` y el borrador apuntan a `clientes`. Un
      // DELETE con cualquiera de esas filas presente aborta la tanda.
      conCliente('cli-1');
      conVentaSinSincronizar('v-1', 'cli-1');

      aplicar([bajaDeCliente('cli-1')]);

      final cliente = db.select('SELECT activo FROM clientes').single;
      expect(cliente['activo'], equals(0));
      expect(
        db.select('SELECT * FROM ventas'),
        hasLength(1),
        reason: 'la venta que el vendedor hizo ayer tiene que poder subir',
      );
    });

    test('y la tanda COMPLETA entra: lo que venía detrás no se pierde', () {
      // Es la prueba de la falla de verdad: el delta de baja iba primero y se
      // llevaba consigo todo lo que venía después, tanda tras tanda.
      conCliente('cli-1');
      conVentaSinSincronizar('v-1', 'cli-1');

      final r = aplicar([bajaDeCliente('cli-1'), productoNuevo('p-nuevo')]);

      expect(r.aplicados, equals(2));
      expect(r.fallidos, equals(0));
      expect(db.select("SELECT * FROM productos WHERE id = 'p-nuevo'"), hasLength(1));
    });

    test('un cliente dado de baja desaparece de la lista de la ruta', () {
      // Lo que esto evita en la calle: mandar al vendedor a la puerta de un
      // cliente que la empresa ya dio por perdido.
      conCliente('cli-1');
      aplicar([bajaDeCliente('cli-1')]);

      final activos = db.select('SELECT id FROM clientes WHERE activo = 1');
      expect(activos, isEmpty);
    });
  });

  // -------------------------------------------------------------------------
  // Hallazgo 1 bis: el estatus del servidor
  // -------------------------------------------------------------------------

  group('el estatus que manda el servidor', () {
    Delta cliente(String estatus) => Delta(
          cursor: 3,
          entidad: 'cliente',
          entidadId: 'cli-2',
          operacion: 'upsert',
          payload: {
            'id': 'cli-2',
            'codigo': 'C-002',
            'nombre_comercial': 'Doña Mary',
            'estatus': estatus,
            'permite_credito': false,
            'limite_credito': 0,
            'bloqueado': false,
          },
        );

    int activoDe(String id) => db
        .select('SELECT activo FROM clientes WHERE id = ?', [id])
        .single['activo'] as int;

    test('«activo» va en la ruta', () {
      aplicar([cliente('activo')]);
      expect(activoDe('cli-2'), equals(1));
    });

    test('«prospecto» TAMBIÉN va: es el alta de la calle sin confirmar', () {
      // Esconderlo sería lo contrario de para qué existe el alta en campo.
      aplicar([cliente('prospecto')]);
      expect(activoDe('cli-2'), equals(1));
    });

    test('«inactivo» y «baja» NO van', () {
      for (final estatus in ['inactivo', 'baja']) {
        aplicar([cliente(estatus)]);
        expect(activoDe('cli-2'), equals(0), reason: 'estatus $estatus');
      }
    });

    test('un estatus que esta versión no conoce cuenta como activo', () {
      // Perder un cliente de la lista por un valor nuevo sería peor que mostrar
      // uno de más: lo segundo se nota de inmediato, lo primero no se nota nunca.
      aplicar([cliente('en_cobranza_judicial')]);
      expect(activoDe('cli-2'), equals(1));
    });

    test('volver a activarlo lo devuelve a la lista', () {
      aplicar([cliente('baja')]);
      aplicar([cliente('activo')]);
      expect(activoDe('cli-2'), equals(1));
    });
  });

  // -------------------------------------------------------------------------
  // Hallazgo 2: un delta que revienta se aparta
  // -------------------------------------------------------------------------

  group('un delta que revienta', () {
    /// Un delta de cartera para un cliente inexistente no revienta —es un UPDATE
    /// que no afecta filas—, así que para probar el aislamiento hace falta uno que
    /// SÍ reviente. Una partida de venta con un producto que no existe lo hace:
    /// la llave foránea de `venta_partidas` no se cumple.
    Delta ventaConProductoFantasma() => Delta(
          cursor: 5,
          entidad: 'venta',
          entidadId: 'v-9',
          operacion: 'upsert',
          payload: {
            'id': 'v-9',
            'estado': 'confirmada',
            'subtotal': '100.00',
            'descuento': '0.00',
            'impuestos': '0.00',
            'total': '100.00',
            'partidas': [
              {
                'id': 'pa-9',
                'linea': 1,
                'producto_id': 'producto-que-no-existe',
                'unidad_codigo': 'PZA',
                'factor_unidad': '1.0000',
                'cantidad': '1.000',
                'cantidad_base': '1.000',
                'precio_unitario': '100.0000',
                'tasa_iva': '0.0000',
                'importe': '100.00',
              },
            ],
          },
        );

    test('NO SE LLEVA LA TANDA, y queda guardado con su error', () {
      conCliente('cli-1');
      conVentaSinSincronizar('v-9', 'cli-1');

      final r = aplicar([ventaConProductoFantasma(), productoNuevo('p-sano')]);

      expect(r.fallidos, equals(1));
      expect(r.aplicados, equals(1));
      // Lo que venía detrás entró.
      expect(db.select("SELECT * FROM productos WHERE id = 'p-sano'"), hasLength(1));

      final apartado = db.select('SELECT * FROM deltas_desconocidos').single;
      expect(apartado['entidad'], equals('venta'));
      expect(apartado['cursor'], equals(5));
      expect(
        apartado['error'],
        contains('FOREIGN KEY'),
        reason: 'sin el error, nadie puede ir a ver por qué falló',
      );
    });

    test('el delta que revienta no deja escrituras a medias', () {
      // El SAVEPOINT es lo que lo garantiza: este delta borra las partidas ANTES
      // de insertar las nuevas, así que sin SAVEPOINT el borrado se habría
      // confirmado y la venta se quedaría sin renglones.
      conCliente('cli-1');
      conVentaSinSincronizar('v-9', 'cli-1');
      db.execute(
        'INSERT INTO productos (id, sku, nombre, unidad_base) '
        "VALUES ('p-real', 'REAL', 'Real', 'PZA')",
      );
      db.execute(
        'INSERT INTO venta_partidas (id, venta_id, linea, producto_id, '
        '       unidad_codigo, factor_unidad, cantidad, cantidad_base, '
        '       precio_unitario, importe) '
        "VALUES ('pa-vieja', 'v-9', 1, 'p-real', 'PZA', 1, 5, 5, 20, 100)",
      );

      aplicar([ventaConProductoFantasma()]);

      expect(
        db.select('SELECT * FROM venta_partidas'),
        hasLength(1),
        reason: 'la partida original se perdió: el SAVEPOINT no deshizo el borrado',
      );
    });

    test('una entidad DESCONOCIDA se distingue de una que reventó', () {
      // Las dos se guardan en la misma tabla y se arreglan de forma distinta: la
      // desconocida se arregla actualizando la app; la que reventó es un defecto.
      final r = aplicar([
        Delta(
          cursor: 7,
          entidad: 'entidad_del_futuro',
          entidadId: 'x',
          operacion: 'upsert',
          payload: {'algo': 1},
        ),
      ]);

      expect(r.desconocidos, equals(1));
      expect(r.fallidos, equals(0));
      expect(
        db.select('SELECT error FROM deltas_desconocidos').single['error'],
        isNull,
      );
    });
  });
}
