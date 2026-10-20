/// Reimprimir el ticket de una venta desde «Mi día» (ADR 0002 §159).
///
/// Lo que defienden estas pruebas:
/// · La que ya se imprimió sale **con los bytes guardados** y «COPIA n»: aunque
///   el catálogo cambie, el papel dice lo mismo que el que recibió el cliente.
/// · La que nunca se imprimió sale como ORIGINAL, idéntica a la que se habría
///   impreso al cobrar, y desde ahí queda guardada.
/// · La que la oficina corrigió sale como quedó, y lo dice.
/// · La cancelada no se reimprime.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
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

  const visita = DatosDeLaVisita(
    nombreCliente: 'Abarrotes Doña Mary',
    nombreVendedor: 'Juan Pérez',
    codigoCliente: 'CLI-001',
    direccionCliente: 'Mercado de Abastos, local 11',
  );

  CierreDeVenta cierre() => CierreDeVenta(
        db: db,
        outbox: Outbox(db),
        folios: RepoFolios(db),
        nuevoUuid: uuid,
        ahora: () => DateTime.parse('2026-09-29T17:42:03.250Z'),
        identidad: identidad,
      );

  /// Dos cajas de sopa, en efectivo.
  VentaGuardada vender() {
    final carrito = const Carrito().agregar(
      PresentacionVendible(
        productoId: 'p-sopa',
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo 70 g',
        unidadCodigo: 'CAJA',
        factor: Factor.deEnteros(24),
        precio: Precio.deTexto('296.0000'),
        listaPreciosId: 'lista-general',
        listaPreciosVersion: 7,
      ),
      Cantidad.deEnteros(2),
      existencias: ExistenciasCamion({'p-sopa': Cantidad.deEnteros(240)}),
    );
    return cierre().cerrar(carrito.carrito, clienteId: 'cli-1');
  }

  TicketParaReimprimir? reimprimir(String id) => ReimpresionDeVentas(db).ticketDe(
        id,
        negocio: negocioDistribucionesSE,
        nombreVendedor: visita.nombreVendedor,
      );

  String texto(List<int> bytes) => decodificar(bytes).texto;

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    db.execute(
      "INSERT INTO clientes (id, codigo, nombre_comercial, direccion, permite_credito, "
      "limite_credito, saldo_cache, bloqueado, es_local, sincronizado) "
      "VALUES ('cli-1', 'CLI-001', 'Abarrotes Doña Mary', "
      "'Mercado de Abastos, local 11', 1, 5000, 0, 0, 0, 1)",
    );
    db.execute(
      "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
      "VALUES ('p-sopa', 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0, 1)",
    );
    db.execute(
      "INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, "
      "carga_id) VALUES ('p-sopa', 240, 240, 'c-del-dia')",
    );
    RepoFolios(db).guardar(
      RangoFolios(tipo: 'venta', desde: 1, hasta: 500, consumidoHasta: 0),
      asignadoEn: '2026-09-29T07:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  test('nunca se imprimió: sale como original, idéntico al de cobrar, y se guarda', () {
    final venta = vender();
    final alCobrar = ticketDeVenta(venta, negocio: negocioDistribucionesSE, visita: visita);

    final t = reimprimir(venta.id)!;
    expect(t.copia, 0);
    expect(t.esCopia, isFalse);
    expect(t.bytes, equals(alCobrar), reason: 'armado desde la base, el mismo papel');

    ReimpresionDeVentas(db).anotarImpresa(venta.id, t);
    final fila = db.select(
      'SELECT impreso, reimpresiones, ticket_escpos FROM ventas WHERE id = ?',
      [venta.id],
    ).single;
    expect(fila['impreso'], 1);
    expect(fila['reimpresiones'], 0);
    expect(fila['ticket_escpos'], equals(alCobrar));

    // La siguiente ya es la copia 1, de los bytes guardados.
    final copia = reimprimir(venta.id)!;
    expect(copia.copia, 1);
    expect(copia.bytes, equals(reimpresion(alCobrar, 1)));
  });

  test('ya impresa: los bytes guardados con «COPIA n», aunque cambie el catálogo', () {
    final venta = vender();
    final original = ticketDeVenta(venta, negocio: negocioDistribucionesSE, visita: visita);
    cierre().marcarImpresa(venta.id, ticket: original);
    db.execute("UPDATE productos SET nombre = 'Sopa nueva 70 g' WHERE id = 'p-sopa'");

    final primera = reimprimir(venta.id)!;
    expect(primera.copia, 1);
    expect(primera.bytes, equals(reimpresion(original, 1)));
    expect(texto(primera.bytes), contains('Sopa de fideo 70 g'));
    expect(texto(primera.bytes), contains('COPIA 1'));

    ReimpresionDeVentas(db).anotarImpresa(venta.id, primera);
    final segunda = reimprimir(venta.id)!;
    expect(segunda.copia, 2);
    expect(texto(segunda.bytes), contains('COPIA 2'));
    expect(
      db.select('SELECT ticket_escpos FROM ventas WHERE id = ?', [venta.id])
          .single['ticket_escpos'],
      equals(original),
      reason: 'el original guardado no cambia con las copias',
    );
  });

  test('corregida por la oficina: sale como quedó, y lo dice', () {
    final venta = vender();
    cierre().marcarImpresa(
      venta.id,
      ticket: ticketDeVenta(venta, negocio: negocioDistribucionesSE, visita: visita),
    );
    // Como la deja el delta de la corrección: una caja en vez de dos.
    db.execute(
      'UPDATE venta_partidas SET cantidad = 1, cantidad_base = 24, importe = 296 '
      'WHERE venta_id = ?',
      [venta.id],
    );
    db.execute(
      "UPDATE ventas SET subtotal = 296, total = 296, "
      "nota_oficina = 'Se regresó una caja' WHERE id = ?",
      [venta.id],
    );

    final t = reimprimir(venta.id)!;
    expect(t.corregida, isTrue);
    expect(t.copia, 1);
    final papel = texto(t.bytes);
    expect(papel, contains('CORREGIDA POR LA OFICINA'));
    expect(papel, contains('COPIA 1'));
    expect(papel, contains('TOTAL 296.00'));
    expect(papel, isNot(contains('592.00')));
  });

  test('la cancelada y la que no existe no se reimprimen', () {
    final venta = vender();
    db.execute("UPDATE ventas SET estado = 'cancelada' WHERE id = ?", [venta.id]);
    expect(reimprimir(venta.id), isNull);
    expect(reimprimir('no-existe'), isNull);
  });

  test('«Mi día» trae el id de cada venta para reimprimirla', () {
    final venta = vender();
    final dia = RepoMiDia(db).delDia(venta.fechaOperativa);
    expect(dia.ventas.single.id, venta.id);
  });
}
