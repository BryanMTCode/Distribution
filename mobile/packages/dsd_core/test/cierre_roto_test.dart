/// Cuando falla algo que no es una regla de negocio.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL BUG QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA
/// ─────────────────────────────────────────────────────────────────────────
/// El cierre sabía rechazar ventas por reglas de la calle —sin existencia, sin
/// folios, crédito negado— y la pantalla sabía decirlas. Lo que no sabía hacer
/// ninguno de los dos era responder cuando la falla no era del negocio: la base
/// tomada por otra operación, el disco lleno. Esa excepción se iba hacia arriba
/// sin que nadie la atrapara, el botón de cobrar se quedaba girando y el
/// vendedor no podía cobrar más en todo el día sin matar la app.
///
/// Así que el cierre promete algo más fuerte que «no lanzo»: lanza `CierreRoto`
/// y **dice si la venta quedó escrita**, que es lo único que decide si el
/// vendedor vuelve a cobrar o no.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
  late Outbox outbox;
  late RepoFolios folios;
  var contador = 0;

  String uuid() => 'id-${(++contador).toString().padLeft(4, '0')}';

  const identidad = IdentidadDeVenta(
    vendedorId: 'u-vendedor',
    codigoVendedor: 'VEND01',
    dispositivoId: 'd-poco',
    almacenId: 'a-camion',
    rutaId: 'r-04',
    cargaId: 'c-del-dia',
  );

  CierreDeVenta cierre() => CierreDeVenta(
        db: db,
        outbox: outbox,
        folios: folios,
        nuevoUuid: uuid,
        ahora: () => DateTime.parse('2026-10-05T17:42:03.250Z'),
        identidad: identidad,
      );

  Carrito carrito() {
    final r = const Carrito().agregar(
      PresentacionVendible(
        productoId: 'p-sopa',
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo 70 g',
        unidadCodigo: 'PZA',
        factor: Factor.uno,
        precio: Precio.deTexto('12.0000'),
        listaPreciosId: 'lista-general',
        listaPreciosVersion: 7,
        esDefault: true,
      ),
      Cantidad.deEnteros(3),
      existencias: ExistenciasCamion({'p-sopa': Cantidad.deEnteros(240)}),
    );
    return r.carrito;
  }

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    outbox = Outbox(db);
    folios = RepoFolios(db);
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial, permite_credito, "
      "limite_credito, saldo_cache, bloqueado, es_local, sincronizado) "
      "VALUES ('cli-1', 'Abarrotes Doña Mary', 1, 5000, 0, 0, 0, 1)",
    );
    db.execute(
      "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
      "VALUES ('p-sopa', 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0, 1)",
    );
    db.execute(
      "INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, "
      "carga_id) VALUES ('p-sopa', 240, 240, 'c-del-dia')",
    );
    folios.guardar(
      RangoFolios(tipo: 'venta', desde: 1, hasta: 500, consumidoHasta: 0),
      asignadoEn: '2026-10-05T07:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  test('la base tomada por otra operación: no quedó escrita, y se puede '
      'reintentar', () {
    // Esto es lo que pasa cuando la sincronización está a medio escribir en el
    // momento en que el vendedor toca «Cobrar».
    db.execute('BEGIN IMMEDIATE');

    final roto = _capturar(() => cierre().cerrar(
          carrito(),
          clienteId: 'cli-1',
          creditoPermitido: false,
        ));

    expect(roto, isA<CierreRoto>());
    expect((roto as CierreRoto).quedoEscrita, isFalse);
    expect(roto.folioLocal, equals('VEND01-000001'));
    expect(db.select('SELECT * FROM ventas'), isEmpty);
    expect(db.select('SELECT * FROM outbox'), isEmpty);

    // Y lo que importa en la calle: el segundo intento pasa, con el MISMO
    // folio, porque el primero no quemó nada.
    final guardada = cierre().cerrar(
      carrito(),
      clienteId: 'cli-1',
      creditoPermitido: false,
    );
    expect(guardada.folioLocal, equals('VEND01-000001'));
  });

  test('una escritura imposible a media transacción deja todo como estaba', () {
    // Sin la tabla de la cola, el INSERT del sobre reventará DESPUÉS de haber
    // escrito la venta y descontado el camión. Es la prueba de que el rollback
    // sigue siendo total y de que se reporta como tal.
    db.execute('DROP TABLE outbox');

    final roto = _capturar(() => cierre().cerrar(
          carrito(),
          clienteId: 'cli-1',
          creditoPermitido: false,
        ));

    expect(roto, isA<CierreRoto>());
    expect((roto as CierreRoto).quedoEscrita, isFalse,
        reason: 'el rollback la deshizo: el vendedor puede volver a cobrar');
    expect(db.select('SELECT * FROM ventas'), isEmpty);
    expect(
      db.select('SELECT cant_actual FROM existencias_camion').single[
          'cant_actual'],
      equals(240),
      reason: 'el camión no se quedó sin las 3 piezas de una venta que no existe',
    );
    expect(
      db.select("SELECT consumido_hasta FROM folios_rangos WHERE tipo='venta'")
          .single['consumido_hasta'],
      equals(0),
      reason: 'el folio no se quemó',
    );
  });

  test('una regla de negocio sigue saliendo como VentaRechazada, no como '
      'CierreRoto', () {
    db.execute('DELETE FROM existencias_camion');
    final error = _capturar(() => cierre().cerrar(
          carrito(),
          clienteId: 'cli-1',
          creditoPermitido: false,
        ));
    expect(error, isA<VentaRechazada>());
  });
}

/// Devuelve lo que lanzó [accion], o `null` si no lanzó.
Object? _capturar(void Function() accion) {
  try {
    accion();
    return null;
  } catch (e) {
    return e;
  }
}
