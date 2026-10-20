/// El vendedor cancela su venta porque se equivocó (ADR 0002 §161).
///
/// Lo que defienden estas pruebas:
/// · La mercancía vuelve al camión AHÍ MISMO, sin señal, y la venta queda
///   cancelada con su motivo.
/// · El sobre `venta.cancelar` va detrás de su `venta.crear`.
/// · El delta del servidor que llega después no devuelve la mercancía otra vez.
/// · Sin motivo, después del corte, o ya cancelada, no se cancela ni se toca nada.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _motivo = 'Me equivoqué de cliente, era la de al lado';

void main() {
  late Database db;
  var contador = 0;

  String uuid() => 'id-${(++contador).toString().padLeft(4, '0')}';
  DateTime ahora() => DateTime.parse('2026-09-29T17:42:03.250Z');

  const identidad = IdentidadDeVenta(
    vendedorId: 'u-vendedor',
    codigoVendedor: 'VEND01',
    dispositivoId: 'd-poco',
    almacenId: 'a-camion',
    rutaId: 'r-04',
    cargaId: 'c-del-dia',
  );

  CancelacionDeVenta cancelacion() => CancelacionDeVenta(
        db: db,
        outbox: Outbox(db),
        nuevoUuid: uuid,
        ahora: ahora,
      );

  /// Dos cajas de sopa (48 piezas), en efectivo: $592.
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
    return CierreDeVenta(
      db: db,
      outbox: Outbox(db),
      folios: RepoFolios(db),
      nuevoUuid: uuid,
      ahora: ahora,
      identidad: identidad,
    ).cerrar(carrito.carrito, clienteId: 'cli-1');
  }

  double enCamion() => db.select(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
      ).single['cant_actual'] as double;

  Row venta(String id) => db.select('SELECT * FROM ventas WHERE id = ?', [id]).single;

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial, permite_credito, limite_credito, "
      "saldo_cache, bloqueado, es_local, sincronizado) "
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
    RepoFolios(db).guardar(
      RangoFolios(tipo: 'venta', desde: 1, hasta: 500, consumidoHasta: 0),
      asignadoEn: '2026-09-29T07:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  test('se cancela: la mercancía vuelve al camión y queda con su motivo', () {
    final v = vender();
    expect(enCamion(), 192.0);

    cancelacion().cancelar(v.id, motivo: '  $_motivo  ');

    expect(enCamion(), 240.0, reason: 'para venderla otra vez ahí mismo');
    expect(venta(v.id)['estado'], 'cancelada');
    expect(venta(v.id)['nota_oficina'], _motivo);
    expect(db.select('SELECT * FROM venta_partidas'), isEmpty);
  });

  test('el sobre va detrás de su venta, con el motivo', () {
    final v = vender();
    cancelacion().cancelar(v.id, motivo: _motivo);

    final sobres = db.select('SELECT tipo, entidad_id, payload FROM outbox ORDER BY secuencia');
    expect(sobres.map((s) => s['tipo']), ['venta.crear', 'venta.cancelar']);
    expect(sobres.last['entidad_id'], v.id);
    final op = ((jsonDecode(sobres.last['payload'] as String) as Map)['operaciones'] as List)
        .single as Map;
    expect((op['datos'] as Map)['motivo'], _motivo);
  });

  test('el delta del servidor que llega después no devuelve otra vez', () {
    final v = vender();
    cancelacion().cancelar(v.id, motivo: _motivo);

    // Como lo manda el servidor: cancelada, con sus partidas, dos veces.
    final delta = Delta(
      cursor: 1,
      entidad: 'venta',
      entidadId: v.id,
      operacion: 'upsert',
      payload: {
        'id': v.id,
        'estado': 'cancelada',
        'subtotal': '592.00',
        'descuento': '0.00',
        'impuestos': '0.00',
        'total': '592.00',
        'partidas': [
          {
            'id': 'pa-1',
            'linea': 1,
            'producto_id': 'p-sopa',
            'unidad_codigo': 'CAJA',
            'factor_unidad': '24.0000',
            'cantidad': '2.000',
            'cantidad_base': '48.000',
            'precio_unitario': '296.0000',
            'tasa_iva': '0.0000',
            'importe': '592.00',
          },
        ],
        'cancelacion_motivo': _motivo,
      },
    );
    final aplicador = AplicadorDeltas(db);
    aplicador.aplicar([delta], recibidoEn: '2026-09-29T18:00:00.000Z');
    aplicador.aplicar([delta], recibidoEn: '2026-09-29T18:05:00.000Z');

    expect(enCamion(), 240.0);
    expect(venta(v.id)['estado'], 'cancelada');
  });

  test('sin motivo no se cancela, y no se toca nada', () {
    final v = vender();
    expect(
      () => cancelacion().cancelar(v.id, motivo: 'error'),
      throwsA(isA<CancelacionRechazada>()
          .having((e) => e.motivo, 'motivo', MotivoNoCancelacion.faltaMotivo)),
    );
    expect(venta(v.id)['estado'], 'confirmada');
    expect(enCamion(), 192.0);
    expect(db.select('SELECT * FROM outbox'), hasLength(1));
  });

  test('después del corte de ese día, la cancela la oficina', () {
    final v = vender();
    db.execute(
      "INSERT INTO cortes_vendedor (id, fecha_operativa, efectivo_declarado, "
      "fecha_dispositivo) VALUES ('corte-1', ?, 592, '2026-09-29T23:00:00Z')",
      [v.fechaOperativa],
    );
    expect(cancelacion().porQueNo(v.id), MotivoNoCancelacion.diaCortado);
    expect(
      () => cancelacion().cancelar(v.id, motivo: _motivo),
      throwsA(isA<CancelacionRechazada>()
          .having((e) => e.motivo, 'motivo', MotivoNoCancelacion.diaCortado)),
    );
    expect(venta(v.id)['estado'], 'confirmada');
  });

  test('solo las del día de trabajo: hoy, o ayer en la madrugada', () {
    final v = vender();
    db.execute("UPDATE ventas SET fecha_operativa = '2026-09-29' WHERE id = ?", [v.id]);

    CancelacionDeVenta a(DateTime cuando) =>
        CancelacionDeVenta(db: db, outbox: Outbox(db), nuevoUuid: uuid, ahora: () => cuando);

    expect(a(DateTime(2026, 9, 29, 18)).porQueNo(v.id), isNull);
    // A las 3 de la mañana el día de trabajo todavía es el de ayer (§157).
    expect(a(DateTime(2026, 9, 30, 3)).porQueNo(v.id), isNull);
    expect(a(DateTime(2026, 9, 30, 9)).porQueNo(v.id), MotivoNoCancelacion.otroDia);
    expect(a(DateTime(2026, 10, 2, 3)).porQueNo(v.id), MotivoNoCancelacion.otroDia);
  });

  test('la ya cancelada y la que no existe no se cancelan otra vez', () {
    final v = vender();
    cancelacion().cancelar(v.id, motivo: _motivo);
    expect(cancelacion().porQueNo(v.id), MotivoNoCancelacion.yaCancelada);
    expect(() => cancelacion().cancelar(v.id, motivo: _motivo),
        throwsA(isA<CancelacionRechazada>()));
    expect(enCamion(), 240.0, reason: 'no se devuelve dos veces');
    expect(cancelacion().porQueNo('no-existe'), MotivoNoCancelacion.noExiste);
  });

  test('en «Mi día» sale del efectivo y aparece cancelada, con su motivo', () {
    final v = vender();
    cancelacion().cancelar(v.id, motivo: _motivo);

    final dia = RepoMiDia(db).delDia(v.fechaOperativa);
    expect(dia.ventas, isEmpty);
    expect(dia.efectivo, Dinero.cero);
    final cancelada = dia.tocadasPorOficina.single;
    expect(cancelada.cancelada, isTrue);
    expect(cancelada.nota, _motivo);
    // Y ya no se reimprime.
    expect(
      ReimpresionDeVentas(db).ticketDe(
        v.id,
        negocio: negocioDistribucionesSE,
        nombreVendedor: 'Juan',
      ),
      isNull,
    );
  });
}
