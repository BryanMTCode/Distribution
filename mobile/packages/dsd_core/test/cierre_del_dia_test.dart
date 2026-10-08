/// El cierre del día en el teléfono: el corte, la solicitud de carga y sus
/// tickets (ADR 0002 §82).
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ───────────────────────────────────────────────────────────────────────────
/// · **El conteo es a ciegas.** El resumen no trae lo que el sistema cree que
///   hay: con la cifra a la vista el conteo se vuelve un copiado.
/// · **El corte y la solicitud viajan por la cola**, con el dinero y las
///   cantidades como texto (contracts §1.4) y la solicitud fechada para MAÑANA.
/// · **La nueva solicitud reemplaza a la pendiente**, como en el servidor.
/// · **El delta de la oficina** pone «aceptada», el folio y lo aceptado —o el
///   motivo del rechazo— para que el ticket lo diga.
/// · **Los tickets** dicen lo vendido, el efectivo contra lo vendido en efectivo
///   y el sobrante; el de la carga, lo que pidió y lo que se le cargó.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _atun = 'p-atun';
const _galletas = 'p-galletas';

void main() {
  late Database db;
  late Outbox outbox;
  var contador = 0;

  String uuid() => '0198a2f4-0000-7000-8000-${(++contador).toString().padLeft(12, '0')}';
  // Hora LOCAL: el día operativo es el del calendario de quien vende, y así la
  // prueba no depende de la zona de la máquina que la corre.
  final ahora = DateTime(2026, 10, 7, 18, 30);

  RegistroDeCierre registro() => RegistroDeCierre(
        db: db,
        outbox: outbox,
        dispositivoId: 'd-poco',
        vendedor: 'Juan Pérez',
        codigoVendedor: 'VEND01',
        nuevoUuid: uuid,
        ahora: () => ahora,
      );

  void venta(String id, double total, {String forma = 'efectivo', int sincronizada = 1}) {
    db.execute(
      'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo, '
      'forma_pago, estado, total, fecha_dispositivo, fecha_operativa, sincronizada, creado_en) '
      "VALUES (?, ?, ?, 'c1', 'contado', ?, 'confirmada', ?, ?, '2026-10-07', ?, ?)",
      [id, id.hashCode.abs(), id, forma, total, ahora.toUtc().toIso8601String(), sincronizada,
       ahora.toUtc().toIso8601String()],
    );
  }

  List<Map<String, Object?>> operaciones() => [
        for (final s in outbox.siguienteLote())
          ...(s.payload['operaciones']! as List).cast<Map<String, Object?>>(),
      ];

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    outbox = Outbox(db);

    for (final (id, sku, nombre) in [
      (_atun, 'ATUN-140', 'Atún en agua 140 g'),
      (_galletas, 'GALL-200', 'Galletas 200 g'),
    ]) {
      db.execute(
        "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
        "VALUES (?, ?, ?, 'PZA', 0, 1)",
        [id, sku, nombre],
      );
      db.execute(
        "INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default) "
        "VALUES (?, 'PZA', 1, 1), (?, 'CAJA', 24, 0)",
        [id, id],
      );
    }
    db.execute("INSERT INTO clientes (id, nombre_comercial) VALUES ('c1', 'La Esquina')");
    db.execute(
      'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, carga_id) '
      "VALUES (?, 240, 60, 'carga-1'), (?, 10, 0, 'carga-1')",
      [_atun, _galletas],
    );
    db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('carga_id_activa', 'carga-1')",
    );
    venta('VEND01-000001', 2250);
    venta('VEND01-000002', 500, forma: 'transferencia', sincronizada: 0);
  });

  tearDown(() => db.dispose());

  group('el resumen del día', () {
    test('parte lo vendido en efectivo y transferencia, y dice qué no ha subido', () {
      final r = registro().resumen();
      expect(r.fechaOperativa, '2026-10-07');
      expect(r.ventas, 2);
      expect(r.efectivo, Dinero.deTexto('2250.00'));
      expect(r.transferencias, Dinero.deTexto('500.00'));
      expect(r.vendido, Dinero.deTexto('2750.00'));
      expect(r.sinSincronizar, 1);
      expect(r.cargaId, 'carga-1');
    });

    test('lista lo que hay que contar sin decir cuánto cree el sistema', () {
      final r = registro().resumen();
      expect(r.productos.map((p) => p.nombre), ['Atún en agua 140 g', 'Galletas 200 g']);
      expect(r.productos.first.mayor?.unidad, 'CAJA');
      // No hay campo con la cifra del sistema: el conteo es a ciegas.
      expect(r.productos.first.toString(), isNot(contains('60')));
    });
  });

  group('el corte', () {
    test('se encola con el dinero y el conteo como texto', () {
      final corte = registro().hacerCorte(
        conteo: {_atun: Cantidad.deEnteros(55), _galletas: Cantidad.cero},
        efectivo: Dinero.deTexto('2200.00'),
        observaciones: '  Se me rompió una lata ',
      );
      final op = operaciones().single;
      expect(op['tipo'], 'corte.crear');
      expect(op['entidad_id'], corte.id);
      final datos = op['datos']! as Map<String, Object?>;
      expect(datos['fecha_operativa'], '2026-10-07');
      expect(datos['carga_id'], 'carga-1');
      expect(datos['efectivo_declarado'], '2200.00');
      expect(datos['observaciones'], 'Se me rompió una lata');
      expect(datos['conteo'], [
        {'producto_id': _atun, 'cantidad': '55.000'},
        {'producto_id': _galletas, 'cantidad': '0.000'},
      ]);
      expect(corte.efectivoEsperado, Dinero.deTexto('2250.00'));
      expect(corte.sincronizado, isFalse);
    });

    test('su ticket dice lo vendido, el faltante de efectivo y el sobrante', () {
      final corte = registro().hacerCorte(
        conteo: {_atun: Cantidad.deEnteros(55), _galletas: Cantidad.cero},
        efectivo: Dinero.deTexto('2200.00'),
      );
      final t = corte.textoTicket;
      expect(t, startsWith('*DISTRIBUCIONES SE*\n*CORTE DEL DÍA*\nJuan Pérez (VEND01)'));
      expect(t, contains('Miércoles 7 de octubre · 18:30'));
      expect(t, contains(r'2 ventas: $2,750.00'));
      expect(t, contains(r'Efectivo: $2,250.00'));
      expect(t, contains(r'Transferencia: $500.00'));
      expect(t, contains(r'Faltan $50.00 contra lo vendido en efectivo.'));
      expect(t, contains('• Atún en agua 140 g: 55 PZA'));
      // Lo que se contó en cero no ensucia el ticket.
      expect(t, isNot(contains('Galletas')));
      // Y se guarda para volver a compartirlo igual.
      expect(registro().corteDelDia()!.textoTicket, t);
    });

    test('un conteo negativo es un dedazo y no se guarda', () {
      expect(
        () => registro().hacerCorte(
          conteo: {_atun: Cantidad.deEnteros(-1)},
          efectivo: Dinero.cero,
        ),
        throwsA(isA<CierreNoValido>()),
      );
      expect(operaciones(), isEmpty);
    });

    test('al confirmarse el sobre, el corte queda como subido', () {
      registro().hacerCorte(conteo: {}, efectivo: Dinero.deTexto('2250.00'));
      final sobre = outbox.siguienteLote().single;
      outbox.confirmar([sobre.operacionId], confirmadoEn: '2026-10-08T02:00:00.000Z');
      outbox.marcarDocumentosConfirmados();
      expect(registro().corteDelDia()!.sincronizado, isTrue);
    });
  });

  group('la solicitud de carga', () {
    RenglonPedido cajasDeAtun(int bultos) => RenglonPedido(
          productoId: _atun,
          nombre: 'Atún en agua 140 g',
          unidad: 'CAJA',
          factor: Factor.deEnteros(24),
          bultos: bultos,
        );

    test('es para mañana, amarrada al corte de hoy, con bultos y piezas', () {
      final cierre = registro();
      final corte = cierre.hacerCorte(conteo: {}, efectivo: Dinero.cero);
      final s = cierre.pedirCarga(renglones: [cajasDeAtun(10)]);
      expect(s.fechaOperativa, '2026-10-08');
      expect(s.corteId, corte.id);
      expect(s.pendiente, isTrue);

      final op = operaciones().last;
      expect(op['tipo'], 'solicitud_carga.crear');
      final datos = op['datos']! as Map<String, Object?>;
      expect(datos['fecha_operativa'], '2026-10-08');
      expect(datos['detalle'], [
        {
          'producto_id': _atun,
          'unidad_codigo': 'CAJA',
          'bultos': '10.000',
          'cantidad': '240.000',
        },
      ]);
    });

    test('la nueva reemplaza a la pendiente', () {
      final cierre = registro();
      final primera = cierre.pedirCarga(renglones: [cajasDeAtun(10)]);
      final segunda = cierre.pedirCarga(renglones: [cajasDeAtun(8)]);
      expect(cierre.solicitud(primera.id)!.estado, 'reemplazada');
      expect(cierre.solicitudPara()!.id, segunda.id);
    });

    test('sin renglones no pide nada', () {
      expect(
        () => registro().pedirCarga(renglones: [cajasDeAtun(0)]),
        throwsA(
          isA<CierreNoValido>().having((e) => e.motivo, 'motivo', MotivoNoCierre.sinRenglones),
        ),
      );
    });

    test('su ticket, pendiente', () {
      final cierre = registro();
      final s = cierre.pedirCarga(renglones: [cajasDeAtun(10)]);
      final t = cierre.ticketDe(s);
      expect(t, contains('*SOLICITUD DE CARGA*'));
      expect(t, contains('Para el jueves 8 de octubre'));
      expect(t, contains('Pendiente: la revisa la oficina.'));
      expect(t, contains('• Atún en agua 140 g: 10 CAJA'));
    });
  });

  group('lo que contesta la oficina', () {
    late AplicadorDeltas aplicador;
    setUp(() => aplicador = AplicadorDeltas(db));

    Delta delta(String id, Map<String, Object?> payload) => Delta(
          cursor: 9,
          entidad: 'solicitud_carga',
          entidadId: id,
          operacion: 'upsert',
          payload: payload,
        );

    test('aceptada: folio y lo que se cargó, y el ticket lo dice', () {
      final cierre = registro();
      final s = cierre.pedirCarga(
        renglones: [
          RenglonPedido(productoId: _atun, nombre: 'Atún en agua 140 g', unidad: 'CAJA',
              factor: Factor.deEnteros(24), bultos: 10),
          RenglonPedido(productoId: _galletas, nombre: 'Galletas 200 g', unidad: 'CAJA',
              factor: Factor.deEnteros(24), bultos: 2),
        ],
      );
      final r = aplicador.aplicar([
        delta(s.id, {
          'id': s.id,
          'estado': 'aceptada',
          'fecha_operativa': '2026-10-08',
          'carga_id': 'carga-2',
          'carga_folio': 'CG-000013',
          'resuelta_en': '2026-10-08T02:00:00+00:00',
          'motivo': null,
          'detalle': [
            {'producto_id': _atun, 'unidad_codigo': 'CAJA', 'bultos': '10.000',
             'cantidad': '240.000', 'cantidad_aceptada': '192.000'},
            {'producto_id': _galletas, 'unidad_codigo': 'CAJA', 'bultos': '2.000',
             'cantidad': '48.000', 'cantidad_aceptada': '0.000'},
          ],
        }),
      ], recibidoEn: '2026-10-08T02:05:00.000Z');
      expect(r.aplicados, 1);

      final aceptada = cierre.solicitud(s.id)!;
      expect(aceptada.aceptada, isTrue);
      expect(aceptada.cargaFolio, 'CG-000013');
      final t = cierre.ticketDe(aceptada);
      expect(t, contains('*CARGA ACEPTADA*'));
      expect(t, contains('Carga CG-000013'));
      expect(t, contains('• Atún en agua 140 g: 8 CAJA (pidió 10 CAJA)'));
      expect(t, contains('• Galletas 200 g: pidió 2 CAJA, no se carga'));
      expect(t, contains('1 producto'));
    });

    test('rechazada: el motivo llega al ticket', () {
      final cierre = registro();
      final s = cierre.pedirCarga(renglones: [
        RenglonPedido(productoId: _atun, nombre: 'Atún', unidad: 'CAJA',
            factor: Factor.deEnteros(24), bultos: 1),
      ]);
      aplicador.aplicar([
        delta(s.id, {
          'estado': 'rechazada', 'fecha_operativa': '2026-10-08',
          'motivo': 'Mañana descansas', 'detalle': const [],
        }),
      ], recibidoEn: '2026-10-08T02:05:00.000Z');
      final t = cierre.ticketDe(cierre.solicitud(s.id)!);
      expect(t, contains('RECHAZADA: Mañana descansas'));
    });

    test('una solicitud que el teléfono no tenía se crea con lo que trae', () {
      aplicador.aplicar([
        delta('s-perdida', {
          'estado': 'aceptada',
          'fecha_operativa': '2026-10-08',
          'carga_folio': 'CG-000020',
          'detalle': [
            {'producto_id': _atun, 'unidad_codigo': 'CAJA', 'bultos': '5.000',
             'cantidad': '120.000', 'cantidad_aceptada': '120.000'},
          ],
        }),
      ], recibidoEn: '2026-10-08T02:05:00.000Z');
      final s = registro().solicitud('s-perdida')!;
      expect(s.cargaFolio, 'CG-000020');
      expect(registro().ticketDe(s), contains('• Atún en agua 140 g: 5 CAJA'));
    });
  });

  group('el cliente de la oficina', () {
    test('lee el cierre y arma el ticket de la carga aceptada', () {
      final c = CierreDeVendedor.deJson({
        'vendedor_id': 'v1', 'vendedor_codigo': 'VEND01', 'vendedor': 'Juan Pérez',
        'camion': 'Camión 01',
        'corte': {
          'id': 'k1', 'fecha_operativa': '2026-10-07', 'estado': 'cerrado',
          'efectivo_declarado': '2200.00', 'efectivo_esperado': '2250.00',
          'diferencia_efectivo': '-50.00', 'observaciones': null,
          'recibido_en': '2026-10-08T01:30:00Z', 'resuelto_en': null,
          'liquidacion_folio': 'LQ-000004', 'nota': null,
          'renglones': [
            {'producto_id': _atun, 'sku': 'ATUN-140', 'nombre': 'Atún', 'unidad_base': 'PZA',
             'contada': '55.000', 'contado': true, 'sistema': '60.000', 'diferencia': '-5.000'},
          ],
        },
        'solicitud': {
          'id': '0198a2f4-aaaa-7000-8000-000000000001', 'fecha_operativa': '2026-10-08',
          'estado': 'aceptada', 'observaciones': null, 'recibido_en': '2026-10-08T01:31:00Z',
          'resuelta_en': '2026-10-08T02:00:00Z', 'resuelta_por': 'Gerente',
          'motivo': null, 'carga_folio': 'CG-000013', 'bodega': 'Bodega',
          'renglones': [
            {'producto_id': _atun, 'sku': 'ATUN-140', 'nombre': 'Atún', 'unidad_base': 'PZA',
             'unidad_codigo': 'CAJA', 'factor': '24.000', 'bultos': '10.000',
             'cantidad': '240.000', 'cantidad_aceptada': '240.000', 'en_bodega': '520.000'},
          ],
        },
        'mensaje': 'Listo.',
      });
      expect(c.corte!.diferenciaEfectivo, Dinero.deTexto('-50.00'));
      expect(c.corte!.conDiferencia.single.diferencia, Cantidad.deEnteros(-5));
      final t = c.ticketDeLaCarga!;
      expect(t, contains('*CARGA ACEPTADA*'));
      expect(t, contains('Carga CG-000013 · sale de Bodega'));
      expect(t, contains('• Atún: 10 CAJA'));
      expect(t, endsWith('Solicitud 0198A2F4'));
    });
  });

  test('el día siguiente cruza el fin de mes y de año', () {
    expect(diaSiguiente('2026-10-31'), '2026-11-01');
    expect(diaSiguiente('2026-12-31'), '2027-01-01');
  });

  test('los pesos con miles, desde los centavos', () {
    expect(Dinero.deTexto('1234567.05').enPesos, r'$1,234,567.05');
    expect(Dinero.deTexto('-50.00').enPesos, r'-$50.00');
    expect(Dinero.cero.enPesos, r'$0.00');
  });
}
