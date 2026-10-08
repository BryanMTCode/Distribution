/// La última pieza del camión se vende.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL BUG QUE ESTE ARCHIVO EXISTE PARA QUE NO VUELVA
/// ─────────────────────────────────────────────────────────────────────────
/// `existencias_camion.cant_actual` es REAL. Restarle cantidades venta tras
/// venta deja deriva binaria: la última pieza acaba guardada como
/// 0.9999999999999998. La pantalla la lee con `Cantidad.deBase`, que redondea a
/// milésimas, y le dice al vendedor «queda 1»; la guarda del cierre comparaba el
/// REAL crudo contra 1.0 y la negaba.
///
/// El resultado en la calle no era un error visible: era el catálogo ofreciendo
/// la pieza y el cobro rebotándola, siempre, con el cliente enfrente. Ese es el
/// peor tipo de falla —la pantalla y la base no decían lo mismo—, así que aquí
/// se prueban las dos derivas, por arriba y por abajo, y que el saldo quede
/// limpio para que la deriva no se arrastre al día siguiente.
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

  /// Siembra el camión con el REAL **exacto** que trae la base, deriva incluida.
  void camionCon(num cantActual) => db.execute(
        'INSERT INTO existencias_camion (producto_id, cant_cargada, '
        'cant_actual, carga_id) VALUES (?, 240, ?, ?)',
        ['p-sopa', cantActual, 'c-del-dia'],
      );

  /// Lo que queda según la base, en crudo.
  num quedan() => db
      .select('SELECT cant_actual FROM existencias_camion')
      .single['cant_actual'] as num;

  /// Arma el carrito **como lo arma la pantalla**: con la existencia leída por
  /// `Cantidad.deBase`, que es lo que el vendedor tiene a la vista.
  Carrito carritoDe(int piezas, num comoLaLeeLaPantalla) {
    final r = const Carrito().agregar(
      pieza(),
      Cantidad.deEnteros(piezas),
      existencias: ExistenciasCamion({
        'p-sopa': Cantidad.deBase(comoLaLeeLaPantalla),
      }),
    );
    expect(r.aceptado, isTrue,
        reason: 'el carrito rechazó la última pieza: ${r.motivo}');
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
    folios.guardar(
      RangoFolios(tipo: 'venta', desde: 1, hasta: 500, consumidoHasta: 0),
      asignadoEn: '2026-10-05T07:00:00.000Z',
    );
  });

  tearDown(() => db.dispose());

  group('la última pieza', () {
    test('con el saldo exacto se vende y el camión queda en cero', () {
      camionCon(1);
      final guardada = cierre().cerrar(
        carritoDe(1, 1),
        clienteId: 'cli-1',
      );
      expect(guardada.folioConsecutivo, equals(1));
      expect(quedan(), equals(0));
    });

    test('con deriva POR DEBAJO se vende: la pantalla decía 1', () {
      // Lo que deja restar 0.333 tres veces, o 239 piezas de 240.
      camionCon(0.9999999999999998);
      expect(Cantidad.deBase(0.9999999999999998).textoCorto, equals('1'),
          reason: 'si la pantalla no dijera 1, este caso no sería el bug');

      final guardada = cierre().cerrar(
        carritoDe(1, 0.9999999999999998),
        clienteId: 'cli-1',
      );

      expect(guardada.folioConsecutivo, equals(1));
      // Y el saldo queda limpio: la deriva no pasa al día siguiente.
      expect(quedan(), equals(0));
    });

    test('con deriva POR ARRIBA se vende y no deja un residuo invisible', () {
      camionCon(1.0000000000000002);
      final guardada = cierre().cerrar(
        carritoDe(1, 1.0000000000000002),
        clienteId: 'cli-1',
      );
      expect(guardada.folioConsecutivo, equals(1));
      // Sin el redondeo al escribir, aquí quedaría 2.2e-16 arriba del camión: un
      // producto "con existencia" que ninguna pantalla sabe mostrar.
      expect(quedan(), equals(0));
    });

    test('vender MÁS de la última pieza sigue rechazándose', () {
      camionCon(1);
      expect(
        () => cierre().cerrar(
          // Se fuerza el carrito contra una existencia mentida: lo que se prueba
          // es la guarda del cierre, no la del carrito.
          carritoDe(2, 2),
          clienteId: 'cli-1',
        ),
        throwsA(isA<VentaRechazada>()
            .having((e) => e.motivo, 'motivo', MotivoNoVenta.sinExistencia)),
      );
      expect(quedan(), equals(1), reason: 'nada se descontó');
      expect(db.select('SELECT * FROM ventas'), isEmpty);
      expect(db.select('SELECT * FROM outbox'), isEmpty);
    });

    test('la fracción de granel se respeta: 0.5 kg no se redondea a 1', () {
      camionCon(0.5);
      final r = const Carrito().agregar(
        pieza(),
        Cantidad.deTexto('0.500'),
        existencias: ExistenciasCamion({'p-sopa': Cantidad.deBase(0.5)}),
      );
      expect(r.aceptado, isTrue);
      cierre().cerrar(r.carrito, clienteId: 'cli-1');
      expect(quedan(), equals(0));
    });
  });
}
