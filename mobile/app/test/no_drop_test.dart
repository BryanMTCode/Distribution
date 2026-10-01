/// La visita que no terminó en venta, desde la pantalla.
///
/// Lo que defienden estas pruebas, y que no se ve probando solo el dominio:
///
/// · Que **sin GPS no se pueda guardar**, y que la pantalla lo diga antes de que
///   el vendedor toque el botón. Es el único documento del sistema que el
///   servidor rechaza por falta de ubicación: sin coordenadas, "estuve ahí y no
///   compró" es indistinguible de "no fui".
/// · Que cada falla del GPS se explique **en lenguaje accionable**. El permiso se
///   da en los ajustes, el GPS apagado se prende y el satélite que no responde se
///   arregla saliendo del techado: un "no se pudo obtener la ubicación" no lleva a
///   ninguna de las tres.
/// · Que la nota se **exija cuando el motivo la exige**: "no le interesa el
///   producto" sin explicación no sirve para nada.
/// · Que el motivo sea **catálogo cerrado y en el orden de la oficina**: en la
///   calle, con el cliente esperando, un catálogo alfabético obliga a leer diez
///   opciones para encontrar "cerrado".
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

final gpsBueno = GpsObtenido(
  Ubicacion(
    lat: 20.6736,
    lng: -103.344,
    precisionMetros: 12,
    origen: OrigenUbicacion.gps,
  ),
);

/// Entra, abre la visita del cliente y de ahí el no-drop.
Future<BaseLocal> irAlNoDrop(
  WidgetTester tester, {
  LecturaGps gps = const GpsSinLectura('el satélite no respondió a tiempo'),
  bool conMotivos = true,
  bool conFolios = true,
  int foliosHasta = 500,
  int consumidoHasta = 0,
}) async {
  final base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b);
      sembrarParaCobrar(b);
      if (conMotivos) sembrarMotivos(b);
      if (conFolios) {
        sembrarFolios(
          b,
          tipo: 'no_drop',
          hasta: foliosHasta,
          consumidoHasta: consumidoHasta,
        );
      }
    },
    extras: [
      servicioUbicacionProvider
          .overrideWithValue(ServicioUbicacionFalso.siempre(gps)),
    ],
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));
  await tocar(tester, const Key('menu_de_visita'));
  await tester.tap(find.text('No me compró'));
  await tester.pumpAndSettle();
  return base;
}

Future<void> escoger(WidgetTester tester, Key campo, String valor) async {
  await tocar(tester, campo);
  await tester.tap(find.text(valor).last);
  await tester.pumpAndSettle();
}

void main() {
  group('con GPS', () {
    testWidgets('la visita queda registrada con su geosello', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      await tocar(tester, const Key('registrar_no_drop'));

      expect(db.contar('no_drops'), 1);
      final fila = db.unaFila('SELECT * FROM no_drops');
      expect(fila['motivo_codigo'], 'CERRADO');
      expect(fila['cliente_id'], 'cliente-1');
      expect(fila['lat'], closeTo(20.6736, 0.0001));
      expect(fila['ubicacion_origen'], 'gps');
    });

    testWidgets('queda encolada para el servidor', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      await tocar(tester, const Key('registrar_no_drop'));

      final sobre = db.unaFila(
        "SELECT estado FROM outbox WHERE tipo = 'no_drop.crear'",
      );
      expect(sobre['estado'], 'pendiente');
    });

    testWidgets('no toca el inventario del camión', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      await tocar(tester, const Key('registrar_no_drop'));

      // Un no-drop es un dato de efectividad, no un movimiento: si moviera
      // existencias, cada visita perdida descuadraría la liquidación.
      final camion = db.unaFila(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
      );
      expect(camion['cant_actual'], 240);
    });

    testWidgets('después de guardar se dice para qué sirvió', (tester) async {
      await irAlNoDrop(tester, gps: gpsBueno);

      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      await tocar(tester, const Key('registrar_no_drop'));

      expect(find.textContaining('como si nunca hubieras venido'), findsOneWidget);
      expect(find.byKey(const Key('no_drop_listo')), findsOneWidget);
    });
  });

  group('sin GPS no hay visita', () {
    testWidgets('el botón no se habilita y se explica por qué', (tester) async {
      await irAlNoDrop(tester);

      final boton = tester.widget<FilledButton>(
        find.byKey(const Key('registrar_no_drop')),
      );
      expect(boton.onPressed, isNull);
      expect(
        find.textContaining('no se distingue de una que nunca se hizo'),
        findsOneWidget,
      );
    });

    testWidgets('el permiso negado se dice donde se resuelve', (tester) async {
      await irAlNoDrop(tester, gps: const GpsSinPermiso(definitivo: true));
      expect(find.textContaining('ajustes del teléfono'), findsOneWidget);
    });

    testWidgets('el GPS apagado se dice que se prenda', (tester) async {
      await irAlNoDrop(tester, gps: const GpsApagado());
      expect(find.textContaining('está apagado'), findsOneWidget);
    });

    testWidgets('bajo techo se dice que salga a la calle', (tester) async {
      await irAlNoDrop(tester);
      expect(find.textContaining('sal un momento a la calle'), findsOneWidget);
    });

    testWidgets('se puede volver a leer el GPS sin salir', (tester) async {
      // El satélite tarda, y a veces aparece al segundo intento. Mandar al
      // vendedor a volver a entrar por eso sería absurdo.
      await irAlNoDrop(tester);
      expect(find.byKey(const Key('releer_gps_no_drop')), findsOneWidget);
      await tocar(tester, const Key('releer_gps_no_drop'));
      expect(find.byKey(const Key('bloque_gps_no_drop')), findsOneWidget);
    });
  });

  group('el motivo y su nota', () {
    testWidgets('un motivo que exige nota no pasa sin ella', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await escoger(
        tester,
        const Key('motivo_de_no_drop'),
        'No traigo lo que pidió',
      );
      await tocar(tester, const Key('registrar_no_drop'));

      expect(db.contar('no_drops'), 0);
      expect(find.textContaining('escribas qué pasó'), findsWidgets);
    });

    testWidgets('con la nota sí pasa, y la nota se guarda', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await escoger(
        tester,
        const Key('motivo_de_no_drop'),
        'No traigo lo que pidió',
      );
      await tester.enterText(
        find.byKey(const Key('nota_no_drop')),
        'Pidió la presentación de 2 litros',
      );
      await tocar(tester, const Key('registrar_no_drop'));

      final fila = db.unaFila('SELECT nota FROM no_drops');
      expect(fila['nota'], 'Pidió la presentación de 2 litros');
    });

    testWidgets('un motivo que es culpa nuestra se marca como tal',
        (tester) async {
      await irAlNoDrop(tester, gps: gpsBueno);

      // Categoría 'producto': es lo que permite preguntar cuántas visitas
      // perdidas podemos arreglar nosotros.
      await escoger(
        tester,
        const Key('motivo_de_no_drop'),
        'No traigo lo que pidió',
      );
      expect(find.textContaining('lo podemos arreglar nosotros'), findsOneWidget);
    });

    testWidgets('un motivo del cliente no se nos achaca', (tester) async {
      await irAlNoDrop(tester, gps: gpsBueno);
      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      expect(find.textContaining('lo podemos arreglar nosotros'), findsNothing);
    });

    testWidgets('sin motivo no se guarda', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await tocar(tester, const Key('registrar_no_drop'));
      expect(db.contar('no_drops'), 0);
      expect(find.textContaining('por qué no te compró'), findsWidgets);
    });

    testWidgets('sin catálogo de motivos se dice con palabras', (tester) async {
      await irAlNoDrop(tester, gps: gpsBueno, conMotivos: false);
      expect(find.byKey(const Key('motivo_de_no_drop')), findsNothing);
      expect(find.textContaining('catálogo de motivos'), findsOneWidget);
    });

    testWidgets('el catálogo respeta el orden de la oficina', (tester) async {
      await irAlNoDrop(tester, gps: gpsBueno);
      await tocar(tester, const Key('motivo_de_no_drop'));

      // 'Cerrado' trae orden 10 y 'No traigo lo que pidió' orden 70: alfabético
      // sería el inverso, y el más frecuente tiene que quedar arriba.
      final cerrado = tester.getCenter(find.text('Cerrado').last);
      final agotado = tester.getCenter(find.text('No traigo lo que pidió').last);
      expect(cerrado.dy < agotado.dy, isTrue);
    });
  });

  group('los folios', () {
    testWidgets('sin rango no se registra y se dice qué hacer', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno, conFolios: false);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      await tocar(tester, const Key('registrar_no_drop'));

      expect(db.contar('no_drops'), 0);
      expect(find.textContaining('folios de visita'), findsOneWidget);
    });

    testWidgets('el rango por agotarse se avisa antes de que estorbe',
        (tester) async {
      await irAlNoDrop(tester, gps: gpsBueno, foliosHasta: 10, consumidoHasta: 8);
      expect(find.textContaining('folios de visita'), findsOneWidget);
    });

    testWidgets('los folios de visita son su propia serie', (tester) async {
      final base = await irAlNoDrop(tester, gps: gpsBueno);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_no_drop'), 'Cerrado');
      await tocar(tester, const Key('registrar_no_drop'));

      // Si compartieran contador con las ventas, un no-drop y una remisión
      // podrían traer el mismo número.
      final consumido = db.unaFila(
        "SELECT consumido_hasta FROM folios_rangos WHERE tipo = 'no_drop'",
      );
      expect(consumido['consumido_hasta'], 1);
      final ventas = db.unaFila(
        "SELECT consumido_hasta FROM folios_rangos WHERE tipo = 'venta'",
      );
      expect(ventas['consumido_hasta'], 0);
    });
  });
}
