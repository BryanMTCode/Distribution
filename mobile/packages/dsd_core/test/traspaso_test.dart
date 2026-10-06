/// La devolución de mercancía del camión a la bodega.
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ───────────────────────────────────────────────────────────────────────────
/// Hasta que existió este documento, bajar 18 cajas de un camión eran dos ajustes
/// independientes y ningún papel que atara los dos lados. Lo que se rompe:
///
/// · **Que el camión no baje.** El vendedor ya no trae la mercancía: si el teléfono
///   le sigue ofreciendo esas 18 cajas, se las vende a un cliente y el faltante
///   aparece en su liquidación.
/// · **Que se exija existencia.** Si él dice que bajó 18, bajó 18 (§0.1). Que el
///   conteo del teléfono diga 12 no cambia el hecho físico, y rechazarlo haría que
///   dejara de registrar devoluciones.
/// · **Que el delta de la bodega le devuelva mercancía al camión.** Lo que la
///   bodega no contó NO regresa al camión: está en tránsito. Sumarlo otra vez le
///   regalaría al vendedor piezas que no tiene.
/// · **Que «nadie lo contó» se confunda con «contaron cero».** Son dos cosas
///   distintas y la pantalla las dice con palabras distintas.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _atun = 'p-atun';
const _galletas = 'p-galletas';

void main() {
  late Database db;
  late Outbox outbox;
  late AplicadorDeltas aplicador;
  var contador = 0;

  String uuid() => 'id-${(++contador).toString().padLeft(4, '0')}';

  RegistroDeTraspaso registro() => RegistroDeTraspaso(
        db: db,
        dispositivoId: 'd-poco',
        almacenId: 'a-camion',
        outbox: outbox,
        nuevoUuid: uuid,
        ahora: () => DateTime.parse('2026-10-06T19:15:00.000Z'),
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
    aplicarEsquemaLocal(db);
    outbox = Outbox(db);
    aplicador = AplicadorDeltas(db);

    for (final (id, sku, nombre) in [
      (_atun, 'ATUN-140', 'Atún en agua 140 g'),
      (_galletas, 'GALL-200', 'Galletas 200 g'),
    ]) {
      db.execute(
        "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
        "VALUES (?, ?, ?, 'PZA', 0, 1)",
        [id, sku, nombre],
      );
    }
    db.execute(
      'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual) '
      "VALUES (?, 48, 48), (?, 10, 10)",
      [_atun, _galletas],
    );
  });

  tearDown(() => db.dispose());

  // =========================================================================
  // El camión baja
  // =========================================================================
  group('registrar la devolución', () {
    test('baja el camión por lo declarado', () {
      registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
        ],
      );

      expect(enCamion(_atun), 30);
      // Lo que no se declaró no se toca.
      expect(enCamion(_galletas), 10);
    });

    test('encola el documento con las cantidades como texto de tres decimales', () {
      registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
        ],
        observaciones: 'Lo que no se vendió',
      );

      final enCola = outbox.siguienteLote().single;
      final operacion = (enCola.payload['operaciones']! as List)
          .cast<Map<String, Object?>>()
          .single;
      expect(operacion['tipo'], 'traspaso.crear');
      final datos = operacion['datos']! as Map<String, Object?>;
      final detalle = (datos['detalle']! as List).cast<Map<String, Object?>>();
      expect(detalle.single['cantidad'], '18.000');
      expect(datos['observaciones'], 'Lo que no se vendió');
      expect(datos['almacen_origen_id'], 'a-camion');
    });

    test('nace propuesto y sin folio: el folio lo pone el servidor', () {
      final guardado = registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
        ],
      );

      final fila = db.select(
        'SELECT folio, estado, resuelto_en, sincronizado FROM traspasos WHERE id = ?',
        [guardado.id],
      ).single;
      expect(fila['folio'], isNull);
      expect(fila['estado'], 'propuesto');
      expect(fila['resuelto_en'], isNull);
      expect(fila['sincronizado'], 0);
    });

    test('dos renglones del mismo producto son uno', () {
      final guardado = registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(6)),
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(12)),
        ],
      );

      expect(guardado.renglones, hasLength(1));
      expect(guardado.totalBase, Cantidad.deBase(18));
      expect(enCamion(_atun), 30);
      expect(
        db.select('SELECT count(*) AS n FROM traspaso_detalle').single['n'],
        1,
      );
    });

    test('sin renglones con cantidad no se registra nada', () {
      expect(
        () => registro().registrar(
          renglones: [
            RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.cero),
          ],
        ),
        throwsA(
          isA<TraspasoRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoTraspaso.sinRenglones,
          ),
        ),
      );
      expect(enCamion(_atun), 48);
      expect(outbox.siguienteLote(), isEmpty);
    });

    test('sin camión asignado no se registra: el servidor no sabría de dónde sale', () {
      final sinCamion = RegistroDeTraspaso(
        db: db,
        dispositivoId: 'd-poco',
        outbox: outbox,
        nuevoUuid: uuid,
        ahora: () => DateTime.parse('2026-10-06T19:15:00.000Z'),
      );

      expect(
        () => sinCamion.registrar(
          renglones: [
            RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
          ],
        ),
        throwsA(
          isA<TraspasoRechazado>().having(
            (e) => e.motivo,
            'motivo',
            MotivoNoTraspaso.sinAlmacen,
          ),
        ),
      );
      expect(enCamion(_atun), 48);
    });
  });

  // =========================================================================
  // §0.1 · si él dice que bajó 18, bajó 18
  // =========================================================================
  group('sin guarda de existencia', () {
    test('se registra aunque el camión diga que traía menos', () {
      registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(60)),
        ],
      );

      // El renglón en negativo es la señal honesta: alguien tiene que explicarlo.
      expect(enCamion(_atun), -12);
    });

    test('un producto que el camión no traía crea su renglón en negativo', () {
      db.execute(
        "INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo) "
        "VALUES ('p-nuevo', 'NUEVO', 'Producto nuevo', 'PZA', 0, 1)",
      );
      registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: 'p-nuevo', cantidadBase: Cantidad.deBase(3)),
        ],
      );

      expect(enCamion('p-nuevo'), -3);
    });
  });

  // =========================================================================
  // El delta de la bodega
  // =========================================================================
  group('cuando la bodega la recibe', () {
    Delta recibida(
      String traspasoId, {
      String estado = 'aceptado',
      String? atun = '16.000',
      String? galletas = '4.000',
    }) =>
        Delta(
          cursor: 7,
          entidad: 'traspaso',
          entidadId: traspasoId,
          operacion: 'upsert',
          payload: {
            'id': traspasoId,
            'folio': 'TR-000012',
            'estado': estado,
            'resuelto_en': '2026-10-06T21:30:00.000Z',
            'detalle': [
              {'producto_id': _atun, 'cantidad': '18.000', 'cantidad_recibida': atun},
              {
                'producto_id': _galletas,
                'cantidad': '4.000',
                'cantidad_recibida': galletas,
              },
            ],
          },
        );

    String declarar() => registro()
        .registrar(
          renglones: [
            RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
            RenglonDeTraspaso(productoId: _galletas, cantidadBase: Cantidad.deBase(4)),
          ],
        )
        .id;

    test('el folio y el estado llegan al teléfono', () {
      final id = declarar();
      aplicador.aplicar([recibida(id)], recibidoEn: '2026-10-06T21:35:00.000Z');

      final fila = db.select(
        'SELECT folio, estado, resuelto_en, sincronizado FROM traspasos WHERE id = ?',
        [id],
      ).single;
      expect(fila['folio'], 'TR-000012');
      expect(fila['estado'], 'aceptado');
      expect(fila['resuelto_en'], '2026-10-06T21:30:00.000Z');
      expect(fila['sincronizado'], 1);
    });

    test('NO le devuelve al camión lo que la bodega no contó', () {
      final id = declarar();
      // 48 − 18 = 30 al declarar.
      expect(enCamion(_atun), 30);

      aplicador.aplicar([recibida(id)], recibidoEn: '2026-10-06T21:35:00.000Z');

      // La bodega contó 16 de 18. Las 2 faltantes NO están en el camión: están en
      // tránsito. Sumarlas aquí le regalaría al vendedor mercancía que no trae.
      expect(enCamion(_atun), 30);
    });

    test('lo contado queda al lado de lo declarado, sin borrarlo', () {
      final id = declarar();
      aplicador.aplicar([recibida(id)], recibidoEn: '2026-10-06T21:35:00.000Z');

      final fila = db.select(
        'SELECT cantidad_base, cantidad_recibida FROM traspaso_detalle '
        ' WHERE traspaso_id = ? AND producto_id = ?',
        [id, _atun],
      ).single;
      expect(fila['cantidad_base'], 18);
      expect(fila['cantidad_recibida'], 16);
    });

    test('aplicarlo dos veces no cambia nada: es estado, no diferencia', () {
      final id = declarar();
      aplicador.aplicar([recibida(id)], recibidoEn: '2026-10-06T21:35:00.000Z');
      aplicador.aplicar([recibida(id)], recibidoEn: '2026-10-06T21:40:00.000Z');

      expect(enCamion(_atun), 30);
      expect(
        db.select(
          'SELECT cantidad_recibida FROM traspaso_detalle '
          ' WHERE traspaso_id = ? AND producto_id = ?',
          [id, _atun],
        ).single['cantidad_recibida'],
        16,
      );
    });

    test('un traspaso que este teléfono no tiene se ignora, no se inventa', () {
      final resultado = aplicador.aplicar(
        [recibida('t-de-otro-telefono')],
        recibidoEn: '2026-10-06T21:35:00.000Z',
      );

      expect(resultado.aplicados, 1);
      expect(resultado.fallidos, 0);
      expect(db.select('SELECT count(*) AS n FROM traspasos').single['n'], 0);
    });

    test('«nadie lo contó» no es «contaron cero»', () {
      final id = declarar();
      aplicador.aplicar(
        [recibida(id, atun: null, galletas: '0.000')],
        recibidoEn: '2026-10-06T21:35:00.000Z',
      );

      final filas = {
        for (final f in db.select(
          'SELECT producto_id, cantidad_recibida FROM traspaso_detalle '
          ' WHERE traspaso_id = ?',
          [id],
        ))
          f['producto_id'] as String: f['cantidad_recibida'],
      };
      expect(filas[_atun], isNull, reason: 'sin contar');
      expect(filas[_galletas], 0, reason: 'contaron cero: no llegó nada');
    });
  });

  // =========================================================================
  // La lista que el vendedor ve
  // =========================================================================
  group('las devoluciones recientes', () {
    test('mientras nadie cuenta, lo recibido es desconocido', () {
      registro().registrar(
        renglones: [
          RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
        ],
      );

      final lista = registro().recientes();
      expect(lista, hasLength(1));
      expect(lista.single.declarado, Cantidad.deBase(18));
      expect(lista.single.recibido, isNull);
      expect(lista.single.recibida, isFalse);
      expect(lista.single.hayDiferencia, isFalse);
    });

    test('cuando la bodega cuenta distinto, la diferencia se ve', () {
      final id = registro()
          .registrar(
            renglones: [
              RenglonDeTraspaso(productoId: _atun, cantidadBase: Cantidad.deBase(18)),
            ],
          )
          .id;
      aplicador.aplicar(
        [
          Delta(
            cursor: 7,
            entidad: 'traspaso',
            entidadId: id,
            operacion: 'upsert',
            payload: {
              'folio': 'TR-000012',
              'estado': 'aceptado',
              'resuelto_en': '2026-10-06T21:30:00.000Z',
              'detalle': [
                {
                  'producto_id': _atun,
                  'cantidad': '18.000',
                  'cantidad_recibida': '16.000',
                },
              ],
            },
          ),
        ],
        recibidoEn: '2026-10-06T21:35:00.000Z',
      );

      final renglon = registro().recientes().single;
      expect(renglon.folio, 'TR-000012');
      expect(renglon.recibida, isTrue);
      expect(renglon.recibido, Cantidad.deBase(16));
      expect(renglon.hayDiferencia, isTrue);
    });
  });
}
