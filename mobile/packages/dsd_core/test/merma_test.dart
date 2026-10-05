/// Mermas, devoluciones y no-drops.
///
/// Lo que estas pruebas defienden, y que cuesta mercancía o datos cuando falla:
///
/// · **Los signos opuestos.** Una merma sale del camión; una devolución entra.
///   Equivocarlo produce un descuadre del DOBLE del tamaño de la operación, y no
///   se nota hasta el cierre del día.
/// · **Que una merma se registre aunque el camión diga que no había.** El cartón
///   ya está roto: bloquearla haría que la pérdida apareciera en la liquidación
///   como faltante del vendedor, que es lo que este documento evita.
/// · **Que el no-drop exija ubicación.** Sin ella es indistinguible de una visita
///   que nunca se hizo, y es el único documento cuyo valor entero depende de haber
///   estado ahí.
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

  RegistroDeMerma registroDeMerma() => RegistroDeMerma(
        db: db,
        vendedorId: 'VEND01',
        dispositivoId: 'd-poco',
        almacenId: 'a-camion',
        outbox: outbox,
        folios: folios,
        nuevoUuid: uuid,
        ahora: () => DateTime.parse('2026-09-29T17:42:03.250Z'),
      );

  RegistroDeNoDrop registroDeNoDrop() => RegistroDeNoDrop(
        db: db,
        vendedorId: 'u-vendedor',
        dispositivoId: 'd-poco',
        rutaId: 'r-04',
        outbox: outbox,
        folios: folios,
        nuevoUuid: uuid,
        ahora: () => DateTime.parse('2026-09-29T17:42:03.250Z'),
      );

  Ubicacion donde({OrigenUbicacion origen = OrigenUbicacion.gps}) => Ubicacion(
        lat: 19.4326,
        lng: -99.1332,
        origen: origen,
        precisionMetros: 8.5,
      );

  double enCamion(String producto) {
    final filas = db.select(
      'SELECT cant_actual FROM existencias_camion WHERE producto_id = ?',
      [producto],
    );
    return filas.isEmpty ? 0 : filas.single['cant_actual'] as double;
  }

  setUp(() {
    contador = 0;
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    outbox = Outbox(db);
    folios = RepoFolios(db);

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
      "INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, carga_id) "
      "VALUES ('p-sopa', 240, 240, 'carga-del-dia')",
    );
    // El catálogo cerrado, como lo sincroniza el servidor.
    db.execute(
      "INSERT INTO motivos_merma (codigo, nombre, afecta_vendedor) VALUES "
      "('CADUCADO', 'Producto caducado', 0), "
      "('ROTO', 'Empaque roto', 1), "
      "('DEVOLUCION_CLIENTE', 'Devolución del cliente', 0)",
    );
    db.execute(
      "INSERT INTO motivos_no_drop (codigo, nombre, categoria, requiere_nota, orden) "
      "VALUES ('CERRADO', 'Cerrado', 'cliente', 0, 10), "
      "       ('AGOTADO_EN_CAMION', 'No traigo lo que pidió', 'producto', 1, 70), "
      "       ('NO_SE_VISITO', 'No alcancé a visitarlo', 'vendedor', 1, 100)",
    );

    for (final tipo in ['merma', 'no_drop']) {
      folios.guardar(
        RangoFolios(tipo: tipo, desde: 1, hasta: 1000, consumidoHasta: 0),
        asignadoEn: '2026-09-29T06:00:00.000Z',
      );
    }
  });

  tearDown(() => db.dispose());

  // -------------------------------------------------------------------------
  // Los signos
  // -------------------------------------------------------------------------

  group('mermas y devoluciones', () {
    test('UNA MERMA SACA MERCANCÍA DEL CAMIÓN', () {
      final m = registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(6),
          ),
        ],
      );

      expect(m.tipo, equals(TipoDeMerma.merma));
      expect(m.total, equals(Cantidad.deEnteros(6)));
      expect(enCamion('p-sopa'), equals(234.0));
    });

    test('UNA DEVOLUCIÓN METE MERCANCÍA AL CAMIÓN', () {
      // El signo contrario. Equivocarlo produce un descuadre del doble del tamaño
      // de la operación, y no se nota hasta el cierre del día.
      registroDeMerma().registrar(
        tipo: TipoDeMerma.devolucion,
        motivoCodigo: 'DEVOLUCION_CLIENTE',
        clienteId: 'cli-1',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(6),
          ),
        ],
      );

      expect(enCamion('p-sopa'), equals(246.0));
    });

    test('los signos del tipo son explícitos', () {
      expect(TipoDeMerma.merma.signo, equals(-1));
      expect(TipoDeMerma.devolucion.signo, equals(1));
    });

    test('UNA MERMA SE REGISTRA AUNQUE EL CAMIÓN DIGA QUE NO HABÍA', () {
      // Aquí la regla es la OPUESTA a la de la venta, y es deliberado: el cartón
      // ya está roto. Bloquearla haría que la pérdida apareciera en la liquidación
      // como faltante del vendedor.
      db.execute("UPDATE existencias_camion SET cant_actual = 2");

      final m = registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(3),
          ),
        ],
      );

      expect(m.folioConsecutivo, equals(1));
      // Queda en negativo, y eso es correcto: el que está mal es el conteo.
      expect(enCamion('p-sopa'), equals(-1.0));
    });

    test('una merma de un producto que el camión no traía crea su renglón', () {
      db.execute(
        "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
        "VALUES ('p-atun', 'ATUN', 'Atún', 'PZA', 0, 1)",
      );
      registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'CADUCADO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-atun',
            cantidadBase: Cantidad.deEnteros(4),
          ),
        ],
      );
      expect(enCamion('p-atun'), equals(-4.0));
    });

    test('escribe la merma, su detalle, el sobre y la marca del folio', () {
      registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'CADUCADO',
        observaciones: '  se mojó la tarima  ',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(6),
          ),
        ],
      );

      final merma = db.select('SELECT * FROM mermas').single;
      expect(merma['tipo'], equals('merma'));
      expect(merma['motivo_codigo'], equals('CADUCADO'));
      expect(merma['observaciones'], equals('se mojó la tarima'));
      expect(merma['sincronizada'], equals(0));

      final detalle = db.select('SELECT * FROM merma_detalle').single;
      expect(detalle['producto_id'], equals('p-sopa'));
      expect(detalle['cantidad_base'], equals(6.0));

      final sobre = db.select('SELECT tipo FROM outbox').single;
      expect(sobre['tipo'], equals('merma.crear'));
      expect(folios.leer('merma')!.consumidoHasta, equals(1));
    });

    test('DOS RENGLONES DEL MISMO PRODUCTO SE SUMAN EN UNO', () {
      // `merma_detalle` tiene UNIQUE (merma_id, producto_id) del lado del
      // servidor. Dos renglones del mismo producto —tres piezas de una caja y dos
      // de otra— son el caso normal al capturar; sin agrupar, el sobre caería en
      // cuarentena por una llave duplicada.
      registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(3),
          ),
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(2),
          ),
        ],
      );

      final detalle = db.select('SELECT * FROM merma_detalle');
      expect(detalle, hasLength(1));
      expect(detalle.single['cantidad_base'], equals(5.0));
      expect(enCamion('p-sopa'), equals(235.0));
    });

    test('una devolución SIN cliente se rechaza', () {
      // Sin cliente no se sabe a quién se le recibió, y la oficina no puede
      // revisar si corresponde a una venta suya.
      expect(
        () => registroDeMerma().registrar(
          tipo: TipoDeMerma.devolucion,
          motivoCodigo: 'DEVOLUCION_CLIENTE',
          renglones: [
            RenglonDeMerma(
              productoId: 'p-sopa',
              cantidadBase: Cantidad.deEnteros(6),
            ),
          ],
        ),
        throwsA(
          isA<MermaRechazada>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoMerma.faltaCliente,
          ),
        ),
      );
      // Y no quemó folio.
      expect(folios.leer('merma')!.consumidoHasta, equals(0));
    });

    test('un motivo que no está en el catálogo se rechaza', () {
      // Texto libre son datos que nunca se van a poder analizar.
      expect(
        () => registroDeMerma().registrar(
          tipo: TipoDeMerma.merma,
          motivoCodigo: 'SE_LO_ROBARON_LOS_DUENDES',
          renglones: [
            RenglonDeMerma(
              productoId: 'p-sopa',
              cantidadBase: Cantidad.deEnteros(1),
            ),
          ],
        ),
        throwsA(
          isA<MermaRechazada>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoMerma.motivoDesconocido,
          ),
        ),
      );
    });

    test('sin renglones no hay nada que registrar', () {
      expect(
        () => registroDeMerma().registrar(
          tipo: TipoDeMerma.merma,
          motivoCodigo: 'ROTO',
          renglones: const [],
        ),
        throwsA(
          isA<MermaRechazada>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoMerma.sinRenglones,
          ),
        ),
      );
    });

    test('una cantidad en cero se rechaza', () {
      expect(
        () => registroDeMerma().registrar(
          tipo: TipoDeMerma.merma,
          motivoCodigo: 'ROTO',
          renglones: [
            RenglonDeMerma(productoId: 'p-sopa', cantidadBase: Cantidad.cero),
          ],
        ),
        throwsA(
          isA<MermaRechazada>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoMerma.cantidadInvalida,
          ),
        ),
      );
      // La transacción no corrió: el inventario quedó igual.
      expect(enCamion('p-sopa'), equals(240.0));
    });

    test('el catálogo dice a quién se le carga la pérdida', () {
      // Lo decide la OFICINA en el catálogo, nunca el vendedor al capturar:
      // dejarlo en sus manos sería pedirle que elija si se le cobra.
      final porCodigo = {for (final m in registroDeMerma().motivos()) m.codigo: m};
      expect(porCodigo['ROTO']!.afectaVendedor, isTrue);
      expect(porCodigo['CADUCADO']!.afectaVendedor, isFalse);
    });

    test('la cantidad viaja como string de tres decimales', () {
      registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deTexto('6.500'),
          ),
        ],
      );
      final payload = db.select('SELECT payload FROM outbox').single['payload'];
      expect(payload, contains('"cantidad_base":"6.500"'));
    });

    test('un folio fallido NO deja hueco en la numeración', () {
      final r = registroDeMerma();
      r.registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(1),
          ),
        ],
      );
      expect(
        () => r.registrar(
          tipo: TipoDeMerma.devolucion,
          motivoCodigo: 'DEVOLUCION_CLIENTE',
          renglones: [
            RenglonDeMerma(
              productoId: 'p-sopa',
              cantidadBase: Cantidad.deEnteros(1),
            ),
          ],
        ),
        throwsA(isA<MermaRechazada>()),
      );
      final tercera = r.registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(1),
          ),
        ],
      );
      expect(tercera.folioConsecutivo, equals(2));
    });
  });

  // -------------------------------------------------------------------------
  // No-drops
  // -------------------------------------------------------------------------

  group('no-drops', () {
    test('se registra con su motivo, su visita y su ubicación', () {
      final n = registroDeNoDrop().registrar(
        clienteId: 'cli-1',
        motivoCodigo: 'CERRADO',
        ubicacion: donde(),
      );

      expect(n.folioConsecutivo, equals(1));
      final fila = db.select('SELECT * FROM no_drops').single;
      expect(fila['cliente_id'], equals('cli-1'));
      expect(fila['motivo_codigo'], equals('CERRADO'));
      expect(fila['lat'], closeTo(19.4326, 0.0001));
      expect(fila['ubicacion_origen'], equals('gps'));
      expect(fila['sincronizado'], equals(0));

      final sobre = db.select('SELECT tipo, visita_id FROM outbox').single;
      expect(sobre['tipo'], equals('no_drop.crear'));
      expect(sobre['visita_id'], equals(n.visitaId));
      expect(folios.leer('no_drop')!.consumidoHasta, equals(1));
    });

    test('SIN UBICACIÓN NO SE REGISTRA', () {
      // Es el único documento que la exige, y la razón está en el esquema: sin
      // ella es indistinguible de una visita que nunca se hizo.
      expect(
        () => registroDeNoDrop().registrar(
          clienteId: 'cli-1',
          motivoCodigo: 'CERRADO',
          ubicacion: null,
        ),
        throwsA(
          isA<NoDropRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoNoDrop.faltaUbicacion,
          ),
        ),
      );
      expect(db.select('SELECT * FROM no_drops'), isEmpty);
      expect(folios.leer('no_drop')!.consumidoHasta, equals(0));
    });

    test('un punto ajustado a mano SÍ se acepta, y queda marcado', () {
      // Cuando el satélite no aparece, el camino no es registrarlo sin ubicación:
      // es ajustar el punto a mano. Queda marcado y la oficina puede juzgarlo.
      registroDeNoDrop().registrar(
        clienteId: 'cli-1',
        motivoCodigo: 'CERRADO',
        ubicacion: donde(origen: OrigenUbicacion.manual),
      );
      expect(
        db.select('SELECT ubicacion_origen FROM no_drops').single['ubicacion_origen'],
        equals('manual'),
      );
    });

    test('un motivo que exige nota no pasa sin ella', () {
      // "No le interesa el producto" sin explicación no sirve para nada, y "no
      // alcancé a visitarlo" sin ella es una excusa en blanco.
      expect(
        () => registroDeNoDrop().registrar(
          clienteId: 'cli-1',
          motivoCodigo: 'NO_SE_VISITO',
          ubicacion: donde(),
        ),
        throwsA(
          isA<NoDropRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoNoDrop.faltaNota,
          ),
        ),
      );
    });

    test('con la nota sí pasa, y se guarda limpia', () {
      final n = registroDeNoDrop().registrar(
        clienteId: 'cli-1',
        motivoCodigo: 'NO_SE_VISITO',
        nota: '  se me hizo tarde con el tráfico  ',
        ubicacion: donde(),
      );
      expect(n.nota, equals('se me hizo tarde con el tráfico'));
    });

    test('un motivo fuera del catálogo se rechaza', () {
      expect(
        () => registroDeNoDrop().registrar(
          clienteId: 'cli-1',
          motivoCodigo: 'PORQUE_SI',
          ubicacion: donde(),
        ),
        throwsA(
          isA<NoDropRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoNoDrop.motivoDesconocido,
          ),
        ),
      );
    });

    test('los motivos vienen en el orden que definió la oficina', () {
      // En la calle, con el cliente esperando, un catálogo alfabético obliga a
      // leer diez opciones para encontrar "cerrado".
      final codigos = registroDeNoDrop().motivos().map((m) => m.codigo).toList();
      expect(codigos, equals(['CERRADO', 'AGOTADO_EN_CAMION', 'NO_SE_VISITO']));
    });

    test('la categoría dice si la visita perdida es culpa nuestra', () {
      // Es la pregunta que de verdad importa del reporte.
      final porCodigo = {for (final m in registroDeNoDrop().motivos()) m.codigo: m};
      expect(porCodigo['CERRADO']!.esNuestraCulpa, isFalse);
      expect(porCodigo['AGOTADO_EN_CAMION']!.esNuestraCulpa, isTrue);
      expect(porCodigo['NO_SE_VISITO']!.esNuestraCulpa, isTrue);
    });

    test('un no-drop NO toca el inventario del camión', () {
      // Es una visita que no pasó, no una mercancía que se movió.
      registroDeNoDrop().registrar(
        clienteId: 'cli-1',
        motivoCodigo: 'CERRADO',
        ubicacion: donde(),
      );
      expect(enCamion('p-sopa'), equals(240.0));
    });

    test('los folios de no-drop son su propia serie', () {
      registroDeMerma().registrar(
        tipo: TipoDeMerma.merma,
        motivoCodigo: 'ROTO',
        renglones: [
          RenglonDeMerma(
            productoId: 'p-sopa',
            cantidadBase: Cantidad.deEnteros(1),
          ),
        ],
      );
      final n = registroDeNoDrop().registrar(
        clienteId: 'cli-1',
        motivoCodigo: 'CERRADO',
        ubicacion: donde(),
      );
      expect(n.folioConsecutivo, equals(1));
      expect(folios.leer('merma')!.consumidoHasta, equals(1));
    });

    test('sin rango de folios no se registra', () {
      db.execute("DELETE FROM folios_rangos WHERE tipo = 'no_drop'");
      expect(
        () => registroDeNoDrop().registrar(
          clienteId: 'cli-1',
          motivoCodigo: 'CERRADO',
          ubicacion: donde(),
        ),
        throwsA(
          isA<NoDropRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoNoDrop.sinRangoDeFolios,
          ),
        ),
      );
    });
  });
}
