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

  final pieza = PresentacionVendible(
    productoId: 'p-sopa',
    sku: 'SOPA-70G',
    nombre: 'Sopa de fideo 70 g',
    unidadCodigo: 'PZA',
    factor: Factor.uno,
    precio: Precio.deTexto('13.0000'),
    listaPreciosId: 'lista-general',
    listaPreciosVersion: 7,
  );

  ModificacionDeVenta modificacion() => ModificacionDeVenta(
        db: db,
        outbox: Outbox(db),
        nuevoUuid: uuid,
        ahora: ahora,
      );

  /// La venta como está, con la cantidad del primer renglón cambiada.
  List<RenglonDeVenta> conCajas(String ventaId, int cajas) {
    final r = modificacion().renglonesDe(ventaId).single;
    return [
      RenglonDeVenta(
        partidaId: r.partidaId,
        linea: r.linea.conCantidad(Cantidad.deEnteros(cajas)),
      ),
    ];
  }

  Map<String, Object?> datosDelUltimoSobre() {
    final sobre = db.select('SELECT payload FROM outbox ORDER BY secuencia DESC LIMIT 1').single;
    final op = ((jsonDecode(sobre['payload'] as String) as Map)['operaciones'] as List)
        .single as Map;
    return (op['datos'] as Map).cast<String, Object?>();
  }

  /// Dos cajas de sopa (48 piezas), en efectivo: $592.
  VentaGuardada vender() {
    final carrito = const Carrito().agregar(
      caja,
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
      throwsA(isA<CambioRechazado>()
          .having((e) => e.motivo, 'motivo', MotivoNoSeToca.faltaMotivo)),
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
    expect(cancelacion().porQueNo(v.id), MotivoNoSeToca.diaCortado);
    expect(
      () => cancelacion().cancelar(v.id, motivo: _motivo),
      throwsA(isA<CambioRechazado>()
          .having((e) => e.motivo, 'motivo', MotivoNoSeToca.diaCortado)),
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
    expect(a(DateTime(2026, 9, 30, 9)).porQueNo(v.id), MotivoNoSeToca.otroDia);
    expect(a(DateTime(2026, 10, 2, 3)).porQueNo(v.id), MotivoNoSeToca.otroDia);
  });

  test('la ya cancelada y la que no existe no se cancelan otra vez', () {
    final v = vender();
    cancelacion().cancelar(v.id, motivo: _motivo);
    expect(cancelacion().porQueNo(v.id), MotivoNoSeToca.yaCancelada);
    expect(() => cancelacion().cancelar(v.id, motivo: _motivo),
        throwsA(isA<CambioRechazado>()));
    expect(enCamion(), 240.0, reason: 'no se devuelve dos veces');
    expect(cancelacion().porQueNo('no-existe'), MotivoNoSeToca.noExiste);
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

  test('la cancelación lleva a la pista si el ticket ya se había impreso', () {
    final v = vender();
    CierreDeVenta(
      db: db,
      outbox: Outbox(db),
      folios: RepoFolios(db),
      nuevoUuid: uuid,
      ahora: ahora,
      identidad: identidad,
    )
      ..marcarImpresa(v.id, ticket: [1, 2, 3])
      ..marcarImpresa(v.id, ticket: [1, 2, 3]);

    cancelacion().cancelar(v.id, motivo: _motivo);

    final datos = datosDelUltimoSobre();
    expect(datos['ticket_impreso'], isTrue);
    expect(datos['reimpresiones'], 1);
  });

  // -------------------------------------------------------------------------
  // Modificar: subir, bajar o quitar (§162)
  // -------------------------------------------------------------------------

  group('modificar la venta', () {
    const porQue = 'Me equivoqué de cantidad';

    test('bajar: lo que baja vuelve al camión y el ticket de antes se olvida', () {
      final v = vender();
      CierreDeVenta(
        db: db,
        outbox: Outbox(db),
        folios: RepoFolios(db),
        nuevoUuid: uuid,
        ahora: ahora,
        identidad: identidad,
      ).marcarImpresa(v.id, ticket: [1, 2, 3]);
      final partida = modificacion().renglonesDe(v.id).single.partidaId;

      final total = modificacion().modificar(
        v.id,
        renglones: conCajas(v.id, 1),
        motivo: porQue,
      );

      expect(total.texto, '296.00');
      expect(enCamion(), 216.0, reason: '192 + la caja que ya no se llevó');
      final fila = venta(v.id);
      expect(fila['total'], 296.0);
      expect(fila['modificaciones'], 1);
      expect(fila['impreso'], 0);
      expect(fila['ticket_escpos'], isNull);
      final lineas = db.select('SELECT id, cantidad, importe FROM venta_partidas');
      expect(lineas.single['id'], partida, reason: 'el mismo renglón, con otra cantidad');
      expect(lineas.single['cantidad'], 1.0);
      expect(lineas.single['importe'], 296.0);

      final datos = datosDelUltimoSobre();
      expect(datos['motivo'], porQue);
      expect(datos['ticket_impreso'], isTrue);
      final enviada = (datos['partidas']! as List).single as Map;
      expect(enviada['id'], partida);
      expect(datos['venta_id'], v.id);
      expect(enviada['cantidad'], '1.000');
      expect(enviada['importe'], '296.00');
      expect(
        db.select('SELECT tipo FROM outbox ORDER BY secuencia').map((s) => s['tipo']),
        ['venta.crear', 'venta.modificar'],
      );
    });

    test('subir y agregar otro: sale del camión', () {
      final v = vender();
      modificacion().modificar(
        v.id,
        renglones: [
          ...conCajas(v.id, 3),
          RenglonDeVenta(linea: LineaCarrito(presentacion: pieza, cantidad: Cantidad.deEnteros(5))),
        ],
        motivo: 'El cliente pidió más',
      );
      // 240 − 72 (3 cajas) − 5 piezas.
      expect(enCamion(), 163.0);
      expect(venta(v.id)['total'], 953.0, reason: '888 + 5 × 13');
      final lineas = db.select('SELECT linea, unidad_codigo FROM venta_partidas ORDER BY linea');
      expect(lineas.map((l) => l['unidad_codigo']), ['CAJA', 'PZA']);
    });

    test('quitar un renglón lo devuelve completo', () {
      final v = vender();
      modificacion().modificar(
        v.id,
        renglones: [
          ...conCajas(v.id, 2),
          RenglonDeVenta(linea: LineaCarrito(presentacion: pieza, cantidad: Cantidad.deEnteros(5))),
        ],
        motivo: 'El cliente pidió más',
      );
      expect(enCamion(), 187.0);
      final renglones = modificacion().renglonesDe(v.id);
      modificacion().modificar(
        v.id,
        renglones: [renglones.first],
        motivo: 'El cliente ya no quiso las piezas',
      );
      expect(enCamion(), 192.0);
      expect(venta(v.id)['total'], 592.0);
      expect(db.select('SELECT * FROM venta_partidas'), hasLength(1));
      expect(venta(v.id)['modificaciones'], 2);
    });

    test('quitarlo todo no es modificar, es cancelar; y sin cambios no se guarda', () {
      final v = vender();
      for (final renglones in [<RenglonDeVenta>[], conCajas(v.id, 0)]) {
        expect(
          () => modificacion().modificar(v.id, renglones: renglones, motivo: porQue),
          throwsA(isA<CambioRechazado>()
              .having((e) => e.motivo, 'motivo', MotivoNoSeToca.sinRenglones)),
        );
      }
      expect(
        () => modificacion().modificar(v.id, renglones: conCajas(v.id, 2), motivo: porQue),
        throwsA(isA<CambioRechazado>()
            .having((e) => e.motivo, 'motivo', MotivoNoSeToca.sinCambios)),
      );
      expect(enCamion(), 192.0);
      expect(db.select('SELECT * FROM outbox'), hasLength(1));
    });

    test('no se sube más de lo que trae el camión', () {
      final v = vender();
      // Quedan 192 piezas = 8 cajas; con las 2 de la venta caben 10, no 11.
      expect(
        () => modificacion().modificar(v.id, renglones: conCajas(v.id, 11), motivo: porQue),
        throwsA(isA<CambioRechazado>()
            .having((e) => e.motivo, 'motivo', MotivoNoSeToca.sinExistencia)),
      );
      modificacion().modificar(v.id, renglones: conCajas(v.id, 10), motivo: porQue);
      expect(enCamion(), 0.0);
    });

    test('las mismas reglas que cancelar: motivo, día de trabajo y antes del corte', () {
      final v = vender();
      expect(
        () => modificacion().modificar(v.id, renglones: conCajas(v.id, 1), motivo: 'error'),
        throwsA(isA<CambioRechazado>()
            .having((e) => e.motivo, 'motivo', MotivoNoSeToca.faltaMotivo)),
      );
      db.execute(
        "INSERT INTO cortes_vendedor (id, fecha_operativa, efectivo_declarado, "
        "fecha_dispositivo) VALUES ('corte-1', ?, 592, '2026-09-29T23:00:00Z')",
        [v.fechaOperativa],
      );
      expect(
        () => modificacion().modificar(v.id, renglones: conCajas(v.id, 1), motivo: porQue),
        throwsA(isA<CambioRechazado>()
            .having((e) => e.motivo, 'motivo', MotivoNoSeToca.diaCortado)),
      );
      expect(enCamion(), 192.0);
    });

    test('el ticket nuevo sale como original de la venta modificada, y lo dice', () {
      final v = vender();
      modificacion().modificar(v.id, renglones: conCajas(v.id, 3), motivo: porQue);
      TicketParaReimprimir? ticket() => ReimpresionDeVentas(db).ticketDe(
            v.id,
            negocio: negocioDistribucionesSE,
            nombreVendedor: 'Juan',
          );

      final nuevo = ticket()!;
      expect(nuevo.copia, 0);
      final papel = decodificar(nuevo.bytes).texto;
      expect(papel, contains('MODIFICADA'));
      expect(papel, contains('Sustituye al ticket anterior'));
      expect(papel, contains('3 CAJA x 296.00'));
      expect(papel, contains('TOTAL 888.00'));

      ReimpresionDeVentas(db).anotarImpresa(v.id, nuevo);
      final copia = ticket()!;
      expect(copia.copia, 1);
      expect(decodificar(copia.bytes).texto, contains('MODIFICADA'));
    });

    test('el delta del servidor después no mueve el camión', () {
      final v = vender();
      modificacion().modificar(v.id, renglones: conCajas(v.id, 1), motivo: porQue);
      final id = db.select('SELECT id FROM venta_partidas').single['id'] as String;
      final delta = Delta(
        cursor: 1,
        entidad: 'venta',
        entidadId: v.id,
        operacion: 'upsert',
        payload: {
          'id': v.id,
          'estado': 'confirmada',
          'subtotal': '296.00',
          'descuento': '0.00',
          'impuestos': '0.00',
          'total': '296.00',
          'partidas': [
            {
              'id': id,
              'linea': 1,
              'producto_id': 'p-sopa',
              'unidad_codigo': 'CAJA',
              'factor_unidad': '24.0000',
              'cantidad': '1.000',
              'cantidad_base': '24.000',
              'precio_unitario': '296.0000',
              'tasa_iva': '0.0000',
              'importe': '296.00',
            },
          ],
        },
      );
      AplicadorDeltas(db)
        ..aplicar([delta], recibidoEn: '2026-09-29T18:00:00.000Z')
        ..aplicar([delta], recibidoEn: '2026-09-29T18:05:00.000Z');

      expect(enCamion(), 216.0);
      expect(venta(v.id)['total'], 296.0);
    });

    test('cancelar la modificada devuelve lo que quedaba', () {
      final v = vender();
      modificacion().modificar(v.id, renglones: conCajas(v.id, 3), motivo: porQue);
      expect(enCamion(), 168.0);
      cancelacion().cancelar(v.id, motivo: _motivo);
      expect(enCamion(), 240.0);
    });

    test('«Mi día» dice que se modificó, con el total nuevo', () {
      final v = vender();
      modificacion().modificar(v.id, renglones: conCajas(v.id, 3), motivo: porQue);
      final dia = RepoMiDia(db).delDia(v.fechaOperativa);
      expect(dia.ventas.single.modificaciones, 1);
      expect(dia.ventas.single.total.texto, '888.00');
      expect(dia.efectivo.texto, '888.00');
    });
  });
}
