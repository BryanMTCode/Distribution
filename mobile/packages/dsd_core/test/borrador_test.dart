/// El borrador del carrito.
///
/// Lo que se prueba es lo que pasa cuando el sistema mata la app a media
/// visita: que el vendedor vuelva a lo mismo que tenía, **con los mismos
/// precios**, y que un borrador corrupto no impida abrir la app.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
  late RepoBorrador repo;

  PresentacionVendible caja({String precio = '296.0000'}) =>
      PresentacionVendible(
        productoId: 'p-sopa',
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo 70 g',
        unidadCodigo: 'CAJA',
        factor: Factor.deEnteros(24),
        precio: Precio.deTexto(precio),
        listaPreciosId: 'lista-general',
        listaPreciosVersion: 7,
      );

  PresentacionVendible pieza() => PresentacionVendible(
        productoId: 'p-sopa',
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo 70 g',
        unidadCodigo: 'PZA',
        factor: Factor.uno,
        precio: Precio.deTexto('12.3333'),
        listaPreciosId: 'lista-general',
        listaPreciosVersion: 7,
        esDefault: true,
      );

  ExistenciasCamion camion() =>
      ExistenciasCamion({'p-sopa': Cantidad.deEnteros(240)});

  setUp(() {
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    repo = RepoBorrador(db);
  });

  tearDown(() => db.dispose());

  test('sin borrador guardado no hay nada que restaurar', () {
    expect(repo.leer(), isNull);
  });

  test('el carrito vuelve completo, con sus cantidades', () {
    var c = const Carrito();
    c = c.agregar(caja(), Cantidad.deEnteros(2), existencias: camion()).carrito;
    c = c.agregar(pieza(), Cantidad.deEnteros(3), existencias: camion()).carrito;

    repo.guardar('cli-1', c, ahora: '2026-09-29T10:00:00.000Z');
    final vuelto = repo.leer()!;

    expect(vuelto.clienteId, equals('cli-1'));
    expect(vuelto.carrito.cuantasLineas, equals(2));
    expect(vuelto.carrito.total, equals(c.total));
    expect(vuelto.carrito.cantidadDe('p-sopa|CAJA'), equals(Cantidad.deEnteros(2)));
    expect(vuelto.carrito.cantidadDe('p-sopa|PZA'), equals(Cantidad.deEnteros(3)));
  });

  test('conserva el PRECIO con el que se armó, no el del catálogo de ahora', () {
    // Si un pull refresca el catálogo a media visita, el vendedor tiene que
    // seguir viendo lo que le cotizó al cliente. Cambiarle los números en
    // pantalla mientras el cliente los mira es indefendible.
    final c = const Carrito()
        .agregar(caja(precio: '296.0000'), Cantidad.deEnteros(1),
            existencias: camion())
        .carrito;
    repo.guardar('cli-1', c, ahora: '2026-09-29T10:00:00.000Z');

    final vuelto = repo.leer()!;
    expect(
      vuelto.carrito.lineas.single.presentacion.precio,
      equals(Precio.deTexto('296.0000')),
    );
    expect(vuelto.carrito.total, equals(Dinero.deTexto('296.00')));
  });

  test('conserva la forma de pago y la referencia', () {
    final c = const Carrito()
        .conFormaDePago(FormaDePago.transferencia, referencia: 'SPEI 4471')
        .agregar(caja(), Cantidad.deEnteros(1), existencias: camion())
        .carrito;

    repo.guardar('cli-1', c, ahora: '2026-09-29T10:00:00.000Z');
    final vuelto = repo.leer()!.carrito;
    expect(vuelto.formaDePago, FormaDePago.transferencia);
    expect(vuelto.referenciaPago, 'SPEI 4471');
  });

  test('guardar un carrito vacío borra el borrador', () {
    final c = const Carrito()
        .agregar(caja(), Cantidad.deEnteros(1), existencias: camion())
        .carrito;
    repo.guardar('cli-1', c, ahora: '2026-09-29T10:00:00.000Z');
    expect(repo.leer(), isNotNull);

    repo.guardar('cli-1', const Carrito(), ahora: '2026-09-29T10:05:00.000Z');
    expect(repo.leer(), isNull);
  });

  test('cambiar de cliente reemplaza el borrador, no lo acumula', () {
    // Arrastrar renglones de una tienda a la siguiente sería la forma más
    // rápida de facturarle a quien no pidió nada.
    final a = const Carrito()
        .agregar(caja(), Cantidad.deEnteros(2), existencias: camion())
        .carrito;
    final b = const Carrito()
        .agregar(pieza(), Cantidad.deEnteros(1), existencias: camion())
        .carrito;

    repo.guardar('cli-1', a, ahora: '2026-09-29T10:00:00.000Z');
    repo.guardar('cli-2', b, ahora: '2026-09-29T10:10:00.000Z');

    final vuelto = repo.leer()!;
    expect(vuelto.clienteId, equals('cli-2'));
    expect(vuelto.carrito.cuantasLineas, equals(1));
    expect(
      db.select('SELECT COUNT(*) AS n FROM carrito_borrador').single['n'],
      equals(1),
    );
  });

  test('un borrador ilegible no impide abrir la app', () {
    // Un apagón a media escritura, o un esquema viejo tras actualizar. Se
    // descarta y se empieza de cero: es lo mismo que pasaría sin borrador, y
    // mucho mejor que una app que no abre.
    db.execute(
      "INSERT INTO carrito_borrador (id, cliente_id, lineas_json, "
      "actualizado_en) VALUES (1, 'cli-1', '{no es json', '2026-09-29')",
    );

    expect(repo.leer(), isNull);
    // Y se limpió solo, para no volver a intentarlo en cada arranque.
    expect(
      db.select('SELECT COUNT(*) AS n FROM carrito_borrador').single['n'],
      equals(0),
    );
  });

  test('un borrador con una línea incompleta tampoco tumba la app', () {
    db.execute(
      "INSERT INTO carrito_borrador (id, cliente_id, lineas_json, "
      "actualizado_en) VALUES (1, 'cli-1', "
      "'[{\"cantidad\":\"1.000\"}]', '2026-09-29')",
    );
    expect(repo.leer(), isNull);
  });

  test('limpiar lo deja sin nada', () {
    final c = const Carrito()
        .agregar(caja(), Cantidad.deEnteros(1), existencias: camion())
        .carrito;
    repo.guardar('cli-1', c, ahora: '2026-09-29T10:00:00.000Z');

    repo.limpiar();
    expect(repo.leer(), isNull);
  });
}
