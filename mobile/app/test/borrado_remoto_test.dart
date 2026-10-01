/// El borrado remoto en el teléfono: las dos pantallas y el orden del borrado.
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDEN ESTAS PRUEBAS
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **Con cola pendiente NO se borra nada.** Es la regla entera, y es la que
///    cuesta dinero si se rompe: el motivo real de un borrado casi nunca es un
///    robo, y el teléfono puede traer dentro un día de ventas.
/// 2. **La pantalla de bloqueo dice el NÚMERO.** Es lo que convierte «mi
///    teléfono se bloqueó» en «tengo que conectarme».
/// 3. **El borrado se lleva la credencial Y el archivo.** Borrar las tablas
///    dejaría el archivo recuperable; borrar solo el archivo dejaría una app
///    que entra a una base que no existe.
/// 4. **El equipo borrado no vuelve al login.** Volvería a pedir un PIN contra
///    una credencial que ya no existe, una y otra vez, sin decir por qué.
library;

import 'dart:convert';

import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Transporte que contesta órdenes de borrado y acepta lo que se le mande.
class TransporteConOrden implements Transporte {
  TransporteConOrden({this.borrar = true, this.aceptaPush = true});

  bool borrar;
  bool aceptaPush;

  final List<String> rutas = [];
  int confirmaciones = 0;

  @override
  Future<RespuestaHttp> obtener(
    String ruta, {
    Map<String, String>? parametros,
  }) async {
    rutas.add(ruta);
    if (ruta.startsWith('/v1/dispositivos/mio')) {
      return RespuestaHttp(
        200,
        jsonEncode({
          'estado': borrar ? 'suspendido' : 'activo',
          'borrar': borrar,
          'borrado_motivo': 'el vendedor dejó la empresa',
          'dias_max_offline': 7,
          'dias_sin_sincronizar': 0,
        }),
      );
    }
    return RespuestaHttp(200, jsonEncode({'cursor': 0, 'hay_mas': false, 'cambios': []}));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    rutas.add(ruta);
    if (ruta == '/v1/dispositivos/mio/borrado') {
      confirmaciones++;
      return const RespuestaHttp(204, '');
    }
    if (!aceptaPush) throw const ErrorDeRed('sin conexión');

    final sobres = (cuerpo['sobres'] as List).cast<Map<String, Object?>>();
    return RespuestaHttp(
      200,
      jsonEncode({
        'lote_id': cuerpo['lote_id'],
        'aceptadas': sobres.length,
        'duplicadas': 0,
        'rechazadas': 0,
        'resultados': [
          for (final s in sobres)
            {'operacion_id': s['operacion_id'], 'estado': 'aceptada'},
        ],
      }),
    );
  }
}

void main() {
  group('con cola pendiente', () {
    testWidgets('no se borra nada y la pantalla dice cuántas faltan',
        (tester) async {
      // El caso que cuesta dinero si se hace mal: hay orden de borrado, hay
      // ventas sin subir, y no hay señal para entregarlas.
      final transporte = TransporteConOrden(aceptaPush: false);
      late BaseLocal base;
      base = await montarApp(
        tester,
        credencial: credencialDelServidor(),
        extras: [
          tokenProvider.overrideWith((_) => 'token'),
          transporteProvider.overrideWithValue(transporte),
        ],
        sembrar: (b) {
          sembrarCliente(b, id: 'c1', nombre: 'Doña Mary');
          sembrarPendienteEnCola(b, cuantos: 2);
        },
      );
      await entrarCon(tester, pinCorrecto);

      await tester.tap(find.byKey(const Key('boton_sincronizar')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('equipo_bloqueado')), findsOneWidget);
      // El número es la razón de que el equipo no se haya borrado.
      expect(find.text('2'), findsOneWidget);
      expect(find.textContaining('operaciones todavía sin subir'), findsOneWidget);
      expect(find.textContaining('Nada se ha borrado'), findsOneWidget);

      // Y la base sigue ahí: la prueba de que no se borró nada.
      final cuantos = base.db.select('SELECT count(*) AS n FROM outbox').first;
      expect(cuantos['n'], 2);
      expect(transporte.confirmaciones, 0);
    });

    testWidgets('la pantalla ofrece reintentar, y al conectarse sí borra',
        (tester) async {
      // El equipo bloqueado no es un equipo perdido.
      final transporte = TransporteConOrden(aceptaPush: false);
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        extras: [
          tokenProvider.overrideWith((_) => 'token'),
          transporteProvider.overrideWithValue(transporte),
        ],
        sembrar: (b) {
          sembrarCliente(b, id: 'c1', nombre: 'Doña Mary');
          sembrarPendienteEnCola(b);
        },
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_sincronizar')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('equipo_bloqueado')), findsOneWidget);

      transporte.aceptaPush = true;
      await tester.tap(find.byKey(const Key('boton_entregar_pendientes')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('equipo_borrado')), findsOneWidget);
      expect(transporte.confirmaciones, 1);
    });
  });

  group('con la cola vacía', () {
    testWidgets('se borra, se confirma y se muestra la pantalla final',
        (tester) async {
      final transporte = TransporteConOrden();
      final almacen = AlmacenSeguroEnMemoria();
      await almacen.escribir(
        'credencial_local_v1',
        jsonEncode(credencialDelServidor()),
      );
      await almacen.escribir('llave_base_local', 'una-llave');

      await montarApp(
        tester,
        almacenPropio: almacen,
        extras: [
          tokenProvider.overrideWith((_) => 'token'),
          transporteProvider.overrideWithValue(transporte),
        ],
        sembrar: (b) => sembrarCliente(b, id: 'c1', nombre: 'Doña Mary'),
      );
      await entrarCon(tester, pinCorrecto);

      await tester.tap(find.byKey(const Key('boton_sincronizar')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('equipo_borrado')), findsOneWidget);
      expect(find.textContaining('no se perdió ninguna'), findsOneWidget);
      expect(find.textContaining('el vendedor dejó la empresa'), findsOneWidget);

      // La credencial y la llave de la base, fuera. Sin esto, un teléfono
      // «borrado» seguiría pudiendo entrar sin red.
      expect(await almacen.leer('credencial_local_v1'), isNull);
      expect(await almacen.leer('llave_base_local'), isNull);

      // Y se confirmó: es lo único que prueba que el borrado ocurrió.
      expect(transporte.confirmaciones, 1);
    });

    testWidgets('la pantalla final no ofrece ninguna salida', (tester) async {
      // Un botón que no sirve hace concluir que la app está rota en vez de que
      // el equipo está de baja.
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        extras: [
          tokenProvider.overrideWith((_) => 'token'),
          transporteProvider.overrideWithValue(TransporteConOrden()),
        ],
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_sincronizar')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('equipo_borrado')), findsOneWidget);
      expect(find.byType(FilledButton), findsNothing);
      expect(find.byType(OutlinedButton), findsNothing);
    });

    testWidgets('un equipo borrado no vuelve a la pantalla de PIN',
        (tester) async {
      // Volvería a pedir un PIN contra una credencial que ya no existe, una y
      // otra vez, sin decir por qué.
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        extras: [
          tokenProvider.overrideWith((_) => 'token'),
          transporteProvider.overrideWithValue(TransporteConOrden()),
        ],
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_sincronizar')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('campo_pin')), findsNothing);
      expect(find.byKey(const Key('equipo_borrado')), findsOneWidget);
    });
  });

  group('sin orden de borrado', () {
    testWidgets('la sincronización normal no cambia', (tester) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        extras: [
          tokenProvider.overrideWith((_) => 'token'),
          transporteProvider.overrideWithValue(
            TransporteConOrden(borrar: false),
          ),
        ],
        sembrar: (b) => sembrarCliente(b, id: 'c1', nombre: 'Doña Mary'),
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_sincronizar')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('equipo_borrado')), findsNothing);
      expect(find.byKey(const Key('equipo_bloqueado')), findsNothing);
      expect(find.text('Mi ruta'), findsOneWidget);
    });
  });

  group('BaseLocal.borrarTodo', () {
    test('una base en memoria no tiene archivo que borrar', () {
      // Tiene que poder llamarse dos veces y sobre una base sin archivo: el
      // borrado se reintenta, y reventar ahí dejaría al teléfono a medio
      // limpiar.
      final base = BaseLocal.enMemoria();
      expect(base.borrarTodo(), 0);
      expect(base.borrarTodo(), 0);
    });

    test('vacía las tablas y deja la base USABLE', () {
      // Las dos mitades de un fallo real. La primera versión cerraba la base, y
      // la app reventaba inmediatamente después del borrado: los providers de
      // la pantalla del vendedor se recalculan en el mismo cuadro en que el
      // árbol cambia, y leían una base cerrada. En un teléfono real eso es una
      // pantalla roja justo después de un borrado exitoso — el peor momento
      // posible, porque parece que el borrado falló.
      final base = BaseLocal.enMemoria();
      base.db.execute(
        "INSERT INTO clientes (id, nombre_comercial, sincronizado) "
        "VALUES ('c1', 'Doña Mary', 1)",
      );
      expect(base.db.select('SELECT * FROM clientes'), hasLength(1));

      base.borrarTodo();

      // Vacía…
      expect(base.db.select('SELECT * FROM clientes'), isEmpty);
      // …y legible: leerla no revienta.
      expect(
        base.db.select('SELECT count(*) AS n FROM outbox').first['n'],
        0,
      );
    });
  });
}
