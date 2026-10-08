/// El cierre de la venta.
///
/// Aquí se prueba lo que no se puede provocar con el hardware real y cuesta
/// dinero cuando falla: que las cinco escrituras sean atómicas, que el folio no
/// se duplique ni deje hueco, y que el camión no entregue mercancía que no
/// tiene.
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
        ahora: () => DateTime.parse('2026-09-29T17:42:03.250Z'),
        identidad: identidad,
      );

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

  ExistenciasCamion camion({int sopa = 240}) =>
      ExistenciasCamion({'p-sopa': Cantidad.deEnteros(sopa)});

  Carrito conCajas(
    int cuantas, {
    FormaDePago forma = FormaDePago.efectivo,
    String? referencia,
    int enCamion = 240,
  }) {
    final r = const Carrito().agregar(
      caja(),
      Cantidad.deEnteros(cuantas),
      existencias: camion(sopa: enCamion),
    );
    return r.carrito.conFormaDePago(forma, referencia: referencia);
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
      asignadoEn: '2026-09-29T07:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  group('volver a vincular no rebobina los folios', () {
    // Bug de campo, octubre 2026: al volver a vincular, el servidor devolvía el
    // rango con su `consumido_hasta` sin avanzar; el teléfono lo escribía encima,
    // la siguiente venta tomaba un folio ya usado y el vendedor veía «No se
    // guardó la venta» cada vez.
    test('el mismo rango con lo consumido atrasado no retrocede el contador', () {
      cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      cierre().cerrar(conCajas(1), clienteId: 'cli-1');

      folios.guardar(
        RangoFolios(tipo: 'venta', desde: 1, hasta: 500, consumidoHasta: 0),
        asignadoEn: '2026-10-07T09:00:00.000Z',
      );

      final tercera =
          cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      expect(tercera.folioConsecutivo, equals(3));
    });

    test('aunque el contador guardado esté atrás, manda el último folio escrito', () {
      cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      // Como lo dejaba la versión anterior: el contador rebobinado a mano.
      db.execute("UPDATE folios_rangos SET consumido_hasta = 0 WHERE tipo = 'venta'");

      final siguiente =
          cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      expect(siguiente.folioConsecutivo, equals(2));
      expect(db.select('SELECT COUNT(*) AS n FROM ventas').single['n'], equals(2));
    });

    test('un rango NUEVO sí reemplaza lo consumido', () {
      folios.guardar(
        RangoFolios(tipo: 'venta', desde: 501, hasta: 1000, consumidoHasta: 500),
        asignadoEn: '2026-10-07T09:00:00.000Z',
      );
      final venta =
          cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      expect(venta.folioConsecutivo, equals(501));
    });
  });

  group('la venta se escribe completa', () {
    test('venta, partidas, existencia, cola y folio, en una pasada', () {
      final guardada = cierre().cerrar(
        conCajas(2),
        clienteId: 'cli-1',
      );

      expect(guardada.folioConsecutivo, equals(1));
      expect(guardada.folioLocal, equals('VEND01-000001'));
      expect(guardada.total, equals(Dinero.deTexto('592.00')));

      // 1. La venta.
      final venta = db.select('SELECT * FROM ventas').single;
      expect(venta['folio_local'], equals('VEND01-000001'));
      expect(venta['tipo'], equals('contado'));
      expect(venta['forma_pago'], equals('efectivo'));
      expect(venta['estado'], equals('confirmada'));
      expect(venta['sincronizada'], equals(0));
      expect(venta['total'], equals(592.0));
      expect(venta['lista_precios_version'], equals(7));
      // El sello de la visita, que agrupa venta y cobro del mismo punto.
      expect(venta['visita_id'], isNotNull);

      // 2. Las partidas, con la aritmética congelada.
      final partida = db.select('SELECT * FROM venta_partidas').single;
      expect(partida['linea'], equals(1));
      expect(partida['unidad_codigo'], equals('CAJA'));
      expect(partida['factor_unidad'], equals(24.0));
      expect(partida['cantidad'], equals(2.0));
      expect(partida['cantidad_base'], equals(48.0));
      expect(partida['precio_unitario'], equals(296.0));
      expect(partida['importe'], equals(592.0));
      expect(partida['descuento'], equals(0));

      // 3. El camión: 240 - 48.
      final existencia = db.select('SELECT cant_actual FROM existencias_camion').single;
      expect(existencia['cant_actual'], equals(192.0));

      // 4. La cola.
      final cola = outbox.resumen();
      expect(cola.pendientes, equals(1));
      final sobre = outbox.siguienteLote().single;
      expect(sobre.tipo, equals('venta.crear'));

      // 5. La marca del folio.
      expect(folios.leer('venta')!.consumidoHasta, equals(1));
    });

    test('el sobre lleva el folio, no un cero', () {
      // El folio se toma antes de armar el sobre justamente para esto. Una
      // versión anterior lo rellenaba mutando el payload dentro de la
      // transacción, y eso solo funcionaba por el orden interno de `encolar`:
      // reordenar dos líneas habría mandado al servidor ventas con folio 0.
      cierre().cerrar(conCajas(1), clienteId: 'cli-1');

      final sobre = outbox.siguienteLote().single;
      final operacion = (sobre.payload['operaciones'] as List).first as Map;
      final datos = operacion['datos'] as Map;

      expect(datos['folio_consecutivo'], equals(1));
      expect(datos['folio_local'], equals('VEND01-000001'));
      expect(datos['total'], equals('296.00'));
      expect(datos['tipo'], equals('contado'));
      expect(datos['forma_pago'], equals('efectivo'));
      // Sin transferencia, no viaja referencia de banco.
      expect(datos.containsKey('referencia_pago'), isFalse);
      expect(datos['lista_precios_version'], equals(7));

      final partidas = datos['partidas'] as List;
      expect(partidas, hasLength(1));
      final p = partidas.first as Map;
      // El precio viaja con sus 4 decimales y el importe con 2.
      expect(p['precio_unitario'], equals('296.0000'));
      expect(p['cantidad'], equals('1.000'));
      expect(p['cantidad_base'], equals('24.000'));
      expect(p['importe'], equals('296.00'));
    });

    test('el hash del sobre corresponde a su contenido', () {
      cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      final sobre = outbox.siguienteLote().single;

      // El mismo hash que va a recalcular el servidor. Si no coincidiera, el
      // sobre acabaría en cuarentena con la venta ya impresa.
      expect(sobre.hashPayload, equals(sobre.payload['hash_payload']));
      expect(sobre.hashPayload, hasLength(64));
    });

    test('dos presentaciones del mismo producto descuentan una sola existencia',
        () {
      var c = const Carrito();
      c = c.agregar(caja(), Cantidad.deEnteros(2), existencias: camion()).carrito;
      c = c.agregar(pieza(), Cantidad.deEnteros(3), existencias: camion()).carrito;

      cierre().cerrar(c, clienteId: 'cli-1');

      // 2×24 + 3 = 51 unidades base.
      final existencia =
          db.select('SELECT cant_actual FROM existencias_camion').single;
      expect(existencia['cant_actual'], equals(189.0));
      expect(db.select('SELECT * FROM venta_partidas'), hasLength(2));
    });

    test('por transferencia viaja con su referencia y queda de contado', () {
      // Todo es de contado (ADR 0002 §81): la transferencia es una forma de
      // pagar en el acto, no un crédito.
      final g = cierre().cerrar(
        conCajas(1, forma: FormaDePago.transferencia, referencia: 'SPEI 4471'),
        clienteId: 'cli-1',
      );
      expect(g.formaDePago, FormaDePago.transferencia);
      expect(g.referenciaPago, 'SPEI 4471');

      final venta = db.select(
        'SELECT tipo, forma_pago, referencia_pago, sincronizada FROM ventas',
      ).single;
      expect(
        [venta['tipo'], venta['forma_pago'], venta['referencia_pago'], venta['sincronizada']],
        ['contado', 'transferencia', 'SPEI 4471', 0],
      );
      final datos = ((outbox.siguienteLote().single.payload['operaciones'] as List).first
          as Map)['datos'] as Map;
      expect(datos['forma_pago'], 'transferencia');
      expect(datos['referencia_pago'], 'SPEI 4471');
    });

    test('el geosello se guarda con su precisión', () {
      cierre().cerrar(
        conCajas(1),
        clienteId: 'cli-1',
        ubicacion: Ubicacion(
          lat: 20.6597,
          lng: -103.3496,
          origen: OrigenUbicacion.gps,
          precisionMetros: 8.5,
        ),
      );

      final venta = db.select('SELECT lat, lng, ubicacion_precision_m FROM ventas').single;
      expect(venta['lat'], closeTo(20.6597, 0.0000001));
      expect(venta['ubicacion_precision_m'], equals(8.5));
    });

    test('sin GPS la venta se cierra igual', () {
      // Dentro de un mercado techado no hay satélite, y la venta ocurre de todos
      // modos. Exigir coordenadas sería impedir vender.
      final guardada =
          cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      expect(guardada.ubicacion, isNull);

      final venta = db.select('SELECT lat FROM ventas').single;
      expect(venta['lat'], isNull);
    });
  });

  group('los folios', () {
    test('avanzan uno por venta', () {
      final a = cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      final b = cierre().cerrar(conCajas(1), clienteId: 'cli-1');

      expect(a.folioConsecutivo, equals(1));
      expect(b.folioConsecutivo, equals(2));
      expect(b.folioLocal, equals('VEND01-000002'));
      expect(folios.leer('venta')!.consumidoHasta, equals(2));
    });

    test('sin rango asignado no se puede vender', () {
      db.execute('DELETE FROM folios_rangos');

      expect(
        () => cierre().cerrar(conCajas(1), clienteId: 'cli-1'),
        throwsA(
          isA<VentaRechazada>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoVenta.sinRangoDeFolios,
          ),
        ),
      );
    });

    test('con el rango agotado no se inventa un folio', () {
      // Reimprimir folios que ya están en papel en manos de clientes es el
      // escenario que el rango existe para impedir.
      folios.guardar(
        RangoFolios(tipo: 'venta', desde: 1, hasta: 2, consumidoHasta: 2),
        asignadoEn: '2026-09-29T07:00:00.000Z',
      );

      expect(
        () => cierre().cerrar(conCajas(1), clienteId: 'cli-1'),
        throwsA(
          isA<VentaRechazada>()
              .having((e) => e.motivo, 'motivo', MotivoNoVenta.sinFolios),
        ),
      );
      expect(db.select('SELECT * FROM ventas'), isEmpty);
    });

    test('avisa cuando quedan pocos', () {
      folios.guardar(
        RangoFolios(tipo: 'venta', desde: 1, hasta: 60, consumidoHasta: 20),
        asignadoEn: '2026-09-29T07:00:00.000Z',
      );
      final g = cierre().cerrar(conCajas(1), clienteId: 'cli-1');

      // Quedarse sin folios a media ruta significa no poder vender.
      expect(g.foliosRestantes, equals(39));
      expect(g.pocosFoliosRestantes, isTrue);
    });
  });

  group('atomicidad', () {
    test('si falta existencia no queda NADA: ni venta, ni cola, ni folio', () {
      // El caso real: el carrito se armó con 240 y un pull refrescó la carga a 10
      // antes de confirmar. Es la prueba que justifica toda la transacción.
      db.execute("UPDATE existencias_camion SET cant_actual = 10");

      expect(
        () => cierre().cerrar(conCajas(2), clienteId: 'cli-1'),
        throwsA(
          isA<VentaRechazada>()
              .having((e) => e.motivo, 'motivo', MotivoNoVenta.sinExistencia),
        ),
      );

      expect(db.select('SELECT * FROM ventas'), isEmpty);
      expect(db.select('SELECT * FROM venta_partidas'), isEmpty);
      expect(outbox.resumen().pendientes, equals(0));
      // La existencia no se tocó.
      expect(
        db.select('SELECT cant_actual FROM existencias_camion').single['cant_actual'],
        equals(10.0),
      );
      // Y la marca del folio tampoco: el siguiente intento reutiliza el 1.
      expect(folios.leer('venta')!.consumidoHasta, equals(0));
    });

    test('tras un fallo, la venta siguiente usa el MISMO folio', () {
      // Ni hueco ni duplicado. Un hueco en la numeración impresa es imposible de
      // explicar en una auditoría; un duplicado es peor.
      db.execute("UPDATE existencias_camion SET cant_actual = 10");
      expect(
        () => cierre().cerrar(conCajas(2), clienteId: 'cli-1'),
        throwsA(isA<VentaRechazada>()),
      );

      db.execute("UPDATE existencias_camion SET cant_actual = 240");
      final buena =
          cierre().cerrar(conCajas(2), clienteId: 'cli-1');

      expect(buena.folioConsecutivo, equals(1));
      expect(buena.folioLocal, equals('VEND01-000001'));
    });

    test('un producto que salió de la carga no se vende a medias', () {
      db.execute('DELETE FROM existencias_camion');

      expect(
        () => cierre().cerrar(conCajas(1), clienteId: 'cli-1'),
        throwsA(isA<VentaRechazada>()),
      );
      expect(db.select('SELECT * FROM ventas'), isEmpty);
      expect(outbox.resumen().pendientes, equals(0));
    });
  });

  group('lo que no se puede cerrar', () {
    test('un carrito vacío', () {
      expect(
        () => cierre().cerrar(const Carrito(), clienteId: 'cli-1'),
        throwsA(
          isA<VentaRechazada>()
              .having((e) => e.motivo, 'motivo', MotivoNoVenta.carritoVacio),
        ),
      );
    });

  });

  group('la impresión', () {
    test('la primera marca impreso y congela el ticket', () {
      // Decisión de negocio: el vendedor toca "Imprimir" DESPUÉS de que la venta
      // se guardó. Si fuera automática y la impresora estuviera sin papel, la
      // venta ya estaría escrita y no habría dónde reintentar.
      final g = cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      expect(db.select('SELECT impreso FROM ventas').single['impreso'], equals(0));

      cierre().marcarImpresa(g.id, ticket: [27, 64, 72, 79, 76, 65]);

      final venta = db.select('SELECT impreso, reimpresiones, ticket_escpos FROM ventas').single;
      expect(venta['impreso'], equals(1));
      expect(venta['reimpresiones'], equals(0));
      expect(venta['ticket_escpos'], isNotNull);
    });

    test('la segunda cuenta como reimpresión y no cambia el ticket', () {
      // Una reimpresión tiene que salir IDÉNTICA al original —marcada como
      // copia—, así que el payload congelado no se vuelve a calcular.
      final g = cierre().cerrar(conCajas(1), clienteId: 'cli-1');
      cierre().marcarImpresa(g.id, ticket: [1, 2, 3]);
      cierre().marcarImpresa(g.id, ticket: [9, 9, 9]);
      cierre().marcarImpresa(g.id, ticket: [9, 9, 9]);

      final venta = db.select('SELECT reimpresiones, ticket_escpos FROM ventas').single;
      expect(venta['reimpresiones'], equals(2));
      expect((venta['ticket_escpos'] as List).toList(), equals([1, 2, 3]));
    });

    test('marcar una venta que no existe es un error de programación', () {
      expect(
        () => cierre().marcarImpresa('no-existe', ticket: [1]),
        throwsA(isA<ArgumentError>()),
      );
    });
  });
}
