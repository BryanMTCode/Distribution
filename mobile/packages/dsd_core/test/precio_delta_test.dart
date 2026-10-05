/// Un precio que la oficina quitó, aplicado en el teléfono.
///
/// ───────────────────────────────────────────────────────────────────────────
/// EL DEFECTO QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA
/// ───────────────────────────────────────────────────────────────────────────
/// El delta de `precio` se acota por `producto_id`, no por la llave del renglón: un
/// producto tiene un precio por lista y por presentación.
///
/// El servidor mandaba el DELETE con el payload en NULL —correcto para una entidad
/// que se identifica por su `id`, inútil aquí— y el aplicador salía por arriba sin
/// hacer nada. Resultado: la oficina quitaba el precio de CAJA, la pantalla decía
/// «el vendedor ya no la verá», y **el vendedor la seguía viendo para siempre**, al
/// precio que tenía. La venta entraba y el servidor la marcaba con
/// `precio_desactualizado`: una marca que nadie podía entender, porque el precio
/// que el teléfono usó ya no existía en ninguna lista.
///
/// Desde la migración 0033 el payload del borrado trae los tres campos que
/// identifican el renglón.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _lista = 'lista-general';
const _producto = 'p-sopa';

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    aplicador = AplicadorDeltas(db);
    db.execute(
      'INSERT INTO productos (id, sku, nombre, unidad_base) VALUES (?, ?, ?, ?)',
      [_producto, 'SOPA-70G', 'Sopa', 'PZA'],
    );
    for (final (unidad, precio) in [('PZA', 12.3333), ('CAJA', 296.0)]) {
      db.execute(
        'INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) '
        'VALUES (?, ?, ?, ?, 1)',
        [_lista, _producto, unidad, precio],
      );
    }
  });

  tearDown(() => db.dispose());

  Delta borrado(String unidad) => Delta(
        cursor: 1,
        entidad: 'precio',
        entidadId: _producto,
        operacion: 'delete',
        payload: {
          'lista_id': _lista,
          'producto_id': _producto,
          'unidad_codigo': unidad,
        },
      );

  void aplicar(Delta d) =>
      aplicador.aplicar([d], recibidoEn: '2026-10-05T18:00:00.000Z');

  List<String> unidadesConPrecio() => db
      .select(
        'SELECT unidad_codigo FROM precios WHERE producto_id = ? ORDER BY 1',
        [_producto],
      )
      .map((f) => f['unidad_codigo'] as String)
      .toList();

  // -------------------------------------------------------------------------

  test('QUITAR UN PRECIO LO QUITA, y solo ése', () {
    aplicar(borrado('CAJA'));
    expect(unidadesConPrecio(), equals(['PZA']));
  });

  test('quitarlo dos veces no truena', () {
    // Un `pull` repetido trae el mismo delta otra vez. Borrar lo ya borrado es un
    // no-op natural: aquí no hace falta tabla de marcas porque el DELETE es
    // idempotente por sí mismo.
    aplicar(borrado('CAJA'));
    expect(() => aplicar(borrado('CAJA')), returnsNormally);
    expect(unidadesConPrecio(), equals(['PZA']));
  });

  test('un borrado con payload en NULL no borra nada', () {
    // Es el delta que mandaba el servidor viejo. Un teléfono con la app nueva y un
    // servidor sin la migración 0033 no debe borrar el precio equivocado: es mejor
    // seguir con el defecto viejo —una presentación de más— que quitarle al
    // vendedor la que sí puede vender.
    aplicar(
      Delta(
        cursor: 1,
        entidad: 'precio',
        entidadId: _producto,
        operacion: 'delete',
        payload: null,
      ),
    );
    expect(unidadesConPrecio(), equals(['CAJA', 'PZA']));
  });

  test('un upsert sigue guardando el precio', () {
    aplicador.aplicar(
      [
        Delta(
          cursor: 2,
          entidad: 'precio',
          entidadId: _producto,
          operacion: 'upsert',
          payload: {
            'lista_id': _lista,
            'producto_id': _producto,
            'unidad_codigo': 'CAJA',
            'precio': '300.0000',
            'version': 2,
          },
        ),
      ],
      recibidoEn: '2026-10-05T18:00:00.000Z',
    );

    final fila = db.select(
      "SELECT precio FROM precios WHERE producto_id = ? AND unidad_codigo = 'CAJA'",
      [_producto],
    ).single;
    expect(fila['precio'], equals(300.0));
  });
}
