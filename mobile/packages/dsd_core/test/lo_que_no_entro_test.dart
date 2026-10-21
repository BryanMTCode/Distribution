/// Lo que el servidor no aceptó, dicho para el vendedor (ADR 0002 §165).
///
/// El teléfono decía «2 con error» y nada más. El caso que lo destapó: una venta
/// y su cancelación, las dos rechazadas, y nadie podía decir por qué sin entrar
/// al panel.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
  var contador = 0;

  String uuid() => 'id-${(++contador).toString().padLeft(4, '0')}';
  DateTime ahora() => DateTime.parse('2026-10-21T15:13:00.000Z');

  const identidad = IdentidadDeVenta(
    vendedorId: 'u-vendedor',
    codigoVendedor: 'VEND01',
    dispositivoId: 'd-poco',
    almacenId: 'a-camion',
    rutaId: 'r-04',
  );

  final caja = PresentacionVendible(
    productoId: 'p-sopa',
    sku: 'SOPA-70G',
    nombre: 'Sopa de fideo 70 g',
    unidadCodigo: 'CAJA',
    factor: Factor.deEnteros(24),
    precio: Precio.deTexto('296.0000'),
    listaPreciosId: 'lista-general',
    listaPreciosVersion: 7,
  );

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial, permite_credito, limite_credito, "
      "saldo_cache, bloqueado, es_local, sincronizado) "
      "VALUES ('cli-1', 'Abarrotes Doña Mary', 0, 0, 0, 0, 0, 1)",
    );
    db.execute(
      "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
      "VALUES ('p-sopa', 'SOPA-70G', 'Sopa de fideo 70 g', 'PZA', 0, 1)",
    );
    db.execute(
      "INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual) "
      "VALUES ('p-sopa', 240, 240)",
    );
    RepoFolios(db).guardar(
      RangoFolios(tipo: 'venta', desde: 1, hasta: 500, consumidoHasta: 0),
      asignadoEn: '2026-10-21T07:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  test('dice qué venta es, de qué tienda, y lo que contestó el servidor', () {
    final carrito = const Carrito().agregar(
      caja,
      Cantidad.deEnteros(2),
      existencias: ExistenciasCamion({'p-sopa': Cantidad.deEnteros(240)}),
    );
    final venta = CierreDeVenta(
      db: db,
      outbox: Outbox(db),
      folios: RepoFolios(db),
      nuevoUuid: uuid,
      ahora: ahora,
      identidad: identidad,
    ).cerrar(carrito.carrito, clienteId: 'cli-1');
    CancelacionDeVenta(db: db, outbox: Outbox(db), nuevoUuid: uuid, ahora: ahora)
        .cancelar(venta.id, motivo: 'Me equivoqué de tienda, era la de al lado');

    final cola = Outbox(db).siguienteLote();
    Outbox(db)
      ..aCuarentena(
        cola[0].operacionId,
        'conflicto_de_datos: la venta referencia un cliente que no existe: cli-1',
      )
      ..aCuarentena(
        cola[1].operacionId,
        'conflicto_de_datos: la venta a cancelar no existe: ${venta.id}',
      );

    final errores = documentosConError(db);

    expect(errores.map((e) => e.que), [
      'Venta VEND01-000001 · Abarrotes Doña Mary',
      'Cancelación de la venta VEND01-000001 · Abarrotes Doña Mary',
    ]);
    expect(errores.first.motivo, 'la venta referencia un cliente que no existe: cli-1',
        reason: 'sin el código técnico de adelante');
  });

  test('de un lote que el servidor no aceptó, el detalle de su respuesta', () {
    final sobre = SobreLocal(
      operacionId: 'op-1',
      secuencia: 0,
      operaciones: const [
        OperacionLocal(
          tipo: 'cliente.crear',
          entidadId: 'cli-2',
          datos: {'nombre_comercial': 'Abarrotes Lupita'},
        ),
      ],
    );
    Outbox(db)
      ..encolar(sobre, creadoEn: '2026-10-21T15:13:00.000Z', escribirNegocio: (_) {})
      ..aCuarentena('op-1', 'lote_invalido: {"detail":"el lote repite un operacion_id"}');

    final error = documentosConError(db).single;
    expect(error.que, 'Tienda nueva · Abarrotes Lupita');
    expect(error.motivo, 'el lote repite un operacion_id');
  });

  test('sin motivo guardado, lo dice', () {
    final sobre = SobreLocal(
      operacionId: 'op-1',
      secuencia: 0,
      operaciones: const [
        OperacionLocal(tipo: 'merma.crear', entidadId: 'm-1', datos: {}),
      ],
    );
    Outbox(db)
      ..encolar(sobre, creadoEn: '2026-10-21T15:13:00.000Z', escribirNegocio: (_) {})
      ..aCuarentena('op-1', '');
    expect(documentosConError(db).single.motivo, 'El servidor no dijo por qué.');
  });
}
