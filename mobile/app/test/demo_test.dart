/// Modo demo.
///
/// La prueba que importa no es que el modo demo funcione: es que **no pueda
/// llegar a producción**. Un atajo que salta el login en el teléfono de un
/// vendedor es exactamente lo que no debe existir.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/demo.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  group('el cerrojo', () {
    test('la constante se deriva del define, no se puede fijar a mano', () {
      // Esta es la prueba que no se puede eludir: corre igual con define y sin
      // él. Si alguien sustituyera la expresión de demo.dart por un `true`
      // literal —para no tener que pasar el `--dart-define` cada vez—, la
      // corrida normal del CI la atraparía aquí.
      expect(
        modoDemoDisponible,
        equals(!kReleaseMode && const bool.fromEnvironment('DSD_DEMO')),
      );
    });

    test('el modo demo está apagado sin el --dart-define', () {
      // Así se compila normalmente, y así corre el CI. Si esto fallara, el
      // binario que se instala en el teléfono del vendedor podría traer el
      // atajo.
      expect(modoDemoDisponible, isFalse);
      // Con el define puesto la afirmación no aplica: lo que queda cuidando el
      // candado en esa corrida es la prueba de arriba.
    }, skip: modoDemoDisponible ? 'compilado con DSD_DEMO=true' : null);

    testWidgets('sin modo demo el botón no existe en la pantalla',
        (tester) async {
      await montarApp(tester);
      expect(find.byKey(const Key('boton_modo_demo')), findsNothing);
      // Complementaria de demo_activo_test.dart: esta comprueba la ausencia,
      // aquella la presencia. Con el define puesto, la ausencia no aplica.
    }, skip: modoDemoDisponible);
  });

  group('la credencial de demostración', () {
    test('el hash corresponde al PIN, y es un vector compartido', () {
      // El login de demo recorre el camino real de Argon2id: el hash lo generó
      // el servidor con argon2-cffi y está en los vectores compartidos. Si
      // alguien cambia los parámetros, esta prueba lo atrapa.
      final documento = jsonDecode(
        File('../../contracts/argon2_vectors.json').readAsStringSync(),
      ) as Map<String, dynamic>;
      final vector = (documento['vectores'] as List)
          .cast<Map<String, dynamic>>()
          .firstWhere((v) => v['nombre'] == 'pin_numerico');

      expect(pinDemo, equals(vector['password']));
      expect(hashDemo, equals(vector['hash_phc']));
    });

    test('el PIN de demo entra por el camino normal de verificación', () {
      final credencial = CredencialLocal.deJson(
        credencialDemo(ahora: DateTime.utc(2026, 9, 28)),
      );
      expect(
        intentarLoginOffline(credencial, pinDemo, ahora: DateTime.utc(2026, 9, 28)),
        equals(ResultadoLogin.ok),
      );
      expect(
        intentarLoginOffline(credencial, '000000', ahora: DateTime.utc(2026, 9, 28)),
        equals(ResultadoLogin.passwordIncorrecta),
      );
    });

    test('la vigencia alcanza para una evaluación en campo', () {
      final credencial = CredencialLocal.deJson(
        credencialDemo(ahora: DateTime.utc(2026, 9, 28)),
      );
      expect(credencial.diasRestantes(DateTime.utc(2026, 9, 28)), equals(30));
    });
  });

  group('los datos sembrados', () {
    test('cubren los casos que hay que ver en pantalla', () {
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);

      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');

      final clientes = base.db.select(
        'SELECT nombre_comercial, lat, lng FROM clientes ORDER BY secuencia',
      );
      expect(clientes, hasLength(5));
      // Todos con ubicación: la venta los busca por geocerca.
      expect(clientes.every((c) => c['lat'] != null && c['lng'] != null), isTrue);
    });

    test('incluye una transferencia encolada para verla aparte del efectivo', () {
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);
      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');

      final ventas = base.db.select(
        'SELECT tipo, forma_pago FROM ventas WHERE sincronizada = 0',
      );
      expect(ventas, isNotEmpty);
      // Todo es de contado (ADR 0002 §81).
      expect(ventas.every((v) => v['tipo'] == 'contado'), isTrue);
      expect(ventas.any((v) => v['forma_pago'] == 'transferencia'), isTrue);
    });

    test('sembrar dos veces no duplica', () {
      // Se puede pulsar el botón varias veces sin dejar la base en un estado
      // raro.
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);

      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');
      sembrarDemo(base.db, ahora: '2026-09-28T10:05:00.000Z');

      expect(
        base.db.select('SELECT COUNT(*) AS n FROM clientes').single['n'],
        equals(5),
      );
    });

    test('siembran catálogo y carga, o no habría nada que evaluar', () {
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);
      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');

      // Lista por omisión, para poder cotizarle a un cliente sin tarifa.
      expect(
        base.db.select('SELECT id FROM listas_precios WHERE es_default = 1'),
        hasLength(1),
      );

      // Cada producto con precio de pieza; los que van por caja, con los dos.
      final precios = base.db.select(
        'SELECT producto_id, unidad_codigo, precio FROM precios',
      );
      expect(precios.where((p) => p['unidad_codigo'] == 'PZA'), hasLength(6));
      expect(precios.where((p) => p['unidad_codigo'] == 'CAJA'), isNotEmpty);

      // El precio de 4 decimales sobrevive el viaje por REAL: 296.00/24.
      final sopa = base.db.select(
        "SELECT precio FROM precios WHERE producto_id = 'demo-p-sopa' "
        "AND unidad_codigo = 'PZA'",
      ).single;
      expect(
        Precio.deBase((sopa['precio'] as num).toDouble()).texto,
        equals('12.3333'),
      );
    });

    test('distinguen "agotado" de "no va en la carga"', () {
      // Son dos estados distintos y la pantalla los lee distinto: agotado es
      // "se vendió todo", no va en la carga es "la bodega no lo subió".
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);
      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');

      final agotado = base.db.select(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'demo-p-atun'",
      );
      expect(agotado, hasLength(1), reason: 'el atún subió al camión');
      expect((agotado.single['cant_actual'] as num).toDouble(), equals(0));

      final sinCarga = base.db.select(
        "SELECT 1 FROM existencias_camion WHERE producto_id = 'demo-p-jabon'",
      );
      expect(sinCarga, isEmpty, reason: 'el jabón no subió al camión');
    });

    test('sembrar dos veces no infla la carga del camión', () {
      // Si la siembra sumara en vez de fijar, pulsar el botón tres veces daría
      // un camión con el triple de mercancía y el control de existencias
      // dejaría de significar algo.
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);

      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');
      sembrarDemo(base.db, ahora: '2026-09-28T10:05:00.000Z');
      sembrarDemo(base.db, ahora: '2026-09-28T10:10:00.000Z');

      final sopa = base.db.select(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'demo-p-sopa'",
      ).single;
      expect((sopa['cant_actual'] as num).toDouble(), equals(240));
      expect(
        base.db.select('SELECT COUNT(*) AS n FROM productos').single['n'],
        equals(6),
      );
      expect(
        base.db.select('SELECT COUNT(*) AS n FROM precios').single['n'],
        equals(11),
      );
    });

    test('los clientes de demo traen lista, para poder cotizarles', () {
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);
      sembrarDemo(base.db, ahora: '2026-09-28T10:00:00.000Z');

      final sinLista = base.db.select(
        'SELECT id FROM clientes WHERE lista_precios_id IS NULL',
      );
      expect(sinLista, isEmpty);
    });

    test('los vecinos quedan alrededor del punto de referencia', () {
      // Es lo que permite probar el aviso de posible duplicado en el lugar
      // donde estás: el emulador no sirve para eso.
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);

      final aqui = Ubicacion(
        lat: 20.6597,
        lng: -103.3496,
        origen: OrigenUbicacion.gps,
      );
      sembrarDemo(base.db, referencia: aqui, ahora: '2026-09-28T10:00:00.000Z');

      final distancias = base.db
          .select('SELECT lat, lng FROM clientes ORDER BY secuencia')
          .map((f) => aqui.distanciaA(
                Ubicacion(
                  lat: (f['lat'] as num).toDouble(),
                  lng: (f['lng'] as num).toDouble(),
                  origen: OrigenUbicacion.gps,
                ),
              ))
          .toList();

      // Dos dentro del radio de duplicado, y el resto claramente fuera.
      expect(distancias.where((d) => d <= radioDuplicadoMetros), hasLength(2));
      expect(distancias.where((d) => d > 200), isNotEmpty);
    });
  });
}
