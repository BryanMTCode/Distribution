/// Registrar lo que se perdió, desde la pantalla.
///
/// Estas pruebas recorren el camino real: el vendedor va en la ruta, se le
/// revienta una caja entre tienda y tienda, y lo registra antes de que se le
/// olvide.
///
/// Lo que defienden, y que no se ve probando solo el dominio:
///
/// · Que la pantalla **no bloquee** por existencia. Aquí la regla es la opuesta a
///   la del carrito: el cartón ya está roto, y si la pantalla lo impidiera la
///   pérdida aparecería en la liquidación como faltante del vendedor.
/// · Que **se capture en cajas y se guarde en piezas**, con la conversión a la
///   vista: es lo que atrapa el dedazo antes de guardar.
/// · Que el signo sea el correcto en el inventario del camión. Equivocarlo
///   produce un descuadre del doble del tamaño de la operación.
/// · Que un equipo **sin catálogo de motivos** lo diga con palabras en vez de
///   mostrar un desplegable vacío, que parece una falla de la aplicación.
/// · Que al vendedor se le avise cuando el motivo **se le descuenta**: enterarse
///   en la liquidación es lo que rompe la confianza.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra y abre la pantalla de merma del camión (sin cliente).
Future<BaseLocal> irALaMerma(
  WidgetTester tester, {
  bool conMotivos = true,
  bool conFolios = true,
  double existencia = 48,
  int foliosHasta = 500,
  int consumidoHasta = 0,
}) async {
  final base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b, existenciaSopa: existencia);
      sembrarParaCobrar(b);
      if (conMotivos) sembrarMotivos(b);
      if (conFolios) {
        sembrarFolios(
          b,
          tipo: 'merma',
          hasta: foliosHasta,
          consumidoHasta: consumidoHasta,
        );
      }
    },
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('boton_merma'));
  return base;
}

/// Abre la pantalla desde la visita a un cliente, que es el modo devolución.
Future<BaseLocal> irALaDevolucion(WidgetTester tester) async {
  final base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b);
      sembrarParaCobrar(b);
      sembrarMotivos(b);
      sembrarFolios(b, tipo: 'merma');
    },
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));
  await tocar(tester, const Key('menu_de_visita'));
  await tester.tap(find.text('Me devolvió mercancía'));
  await tester.pumpAndSettle();
  return base;
}

Future<void> escoger(WidgetTester tester, Key campo, String valor) async {
  await tocar(tester, campo);
  await tester.tap(find.text(valor).last);
  await tester.pumpAndSettle();
}

/// Captura un renglón completo: escoge el producto y teclea cuántas.
Future<void> capturar(
  WidgetTester tester, {
  String producto = 'p-sopa',
  String cuantas = '2',
  String? unidad,
}) async {
  await tocar(tester, Key('escoger_$producto'));
  if (unidad != null) {
    await escoger(tester, Key('unidad_$producto'), unidad);
  }
  await tester.enterText(find.byKey(Key('cuantas_$producto')), cuantas);
  await tester.pumpAndSettle();
}

void main() {
  group('la captura', () {
    testWidgets('una merma de piezas sale del camión', (tester) async {
      final base = await irALaMerma(tester);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '6');
      await tocar(tester, const Key('registrar_merma'));

      expect(db.contar('mermas'), 1);
      final renglon = db.unaFila('SELECT * FROM merma_detalle');
      expect(renglon['cantidad_base'], 6);

      // 48 − 6 = 42. El signo es lo que descuadra la liquidación si se
      // equivoca.
      final camion = db.unaFila(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
      );
      expect(camion['cant_actual'], 42);
    });

    testWidgets('se captura en cajas y se guarda en piezas', (tester) async {
      final base = await irALaMerma(tester);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '2', unidad: 'CAJA');

      // La conversión se muestra ANTES de guardar: es lo que atrapa el dedazo.
      expect(find.textContaining('48'), findsWidgets);

      await tocar(tester, const Key('registrar_merma'));

      final renglon = db.unaFila('SELECT cantidad_base FROM merma_detalle');
      expect(renglon['cantidad_base'], 48);
    });

    testWidgets('la merma queda encolada para el servidor', (tester) async {
      final base = await irALaMerma(tester);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '3');
      await tocar(tester, const Key('registrar_merma'));

      final sobre = db.unaFila(
        "SELECT tipo, estado FROM outbox WHERE tipo = 'merma.crear'",
      );
      expect(sobre['estado'], 'pendiente');
    });

    testWidgets('quitar un renglón lo saca de la captura', (tester) async {
      final base = await irALaMerma(tester);
      final db = BaseLocalDePrueba(base);

      await capturar(tester, cuantas: '3');
      await tocar(tester, const Key('quitar_p-sopa'));
      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await tocar(tester, const Key('registrar_merma'));

      // Sin renglones no se guarda nada, y la pantalla lo dice.
      expect(db.contar('mermas'), 0);
      expect(find.textContaining('al menos un producto'), findsOneWidget);
    });

    testWidgets('escoger dos veces el mismo producto no duplica el renglón',
        (tester) async {
      await irALaMerma(tester);
      await capturar(tester, cuantas: '3');

      // Ya capturado, deja de ofrecerse: dos campos para lo mismo solo confunden.
      expect(find.byKey(const Key('escoger_p-sopa')), findsNothing);
      expect(find.byKey(const Key('cuantas_p-sopa')), findsOneWidget);
    });
  });

  group('el cartón ya está roto', () {
    testWidgets('mermar más de lo que hay avisa pero no bloquea', (tester) async {
      final base = await irALaMerma(tester, existencia: 10);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '24');

      expect(
        find.textContaining('más de lo que el camión dice'),
        findsOneWidget,
      );

      await tocar(tester, const Key('registrar_merma'));
      expect(db.contar('mermas'), 1);

      // Y la existencia queda NEGATIVA, a propósito: el conteo es el que está
      // mal, y mentir sobre el inventario sería peor.
      final camion = db.unaFila(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
      );
      expect((camion['cant_actual'] as num) < 0, isTrue);
    });

    testWidgets('un renglón en cero no se guarda y se dice cuál', (tester) async {
      final base = await irALaMerma(tester);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '0');
      await tocar(tester, const Key('registrar_merma'));

      expect(db.contar('mermas'), 0);
      expect(find.textContaining('Sopa de fideo'), findsWidgets);
    });

    testWidgets('sin motivo no se guarda', (tester) async {
      final base = await irALaMerma(tester);
      final db = BaseLocalDePrueba(base);

      await capturar(tester, cuantas: '3');
      await tocar(tester, const Key('registrar_merma'));

      expect(db.contar('mermas'), 0);
      expect(find.textContaining('por qué se perdió'), findsOneWidget);
    });
  });

  group('lo que la pantalla le dice al vendedor', () {
    testWidgets('un motivo que se le descuenta se le avisa', (tester) async {
      await irALaMerma(tester);

      // 'ROTO' trae afecta_vendedor = 1. Enterarse en la liquidación es lo que
      // rompe la confianza.
      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      expect(find.textContaining('se te descuenta'), findsOneWidget);
    });

    testWidgets('un motivo que no se le descuenta no lo asusta', (tester) async {
      await irALaMerma(tester);

      await escoger(tester, const Key('motivo_de_merma'), 'Producto caducado');
      expect(find.textContaining('se te descuenta'), findsNothing);
    });

    testWidgets('sin catálogo de motivos se dice con palabras', (tester) async {
      await irALaMerma(tester, conMotivos: false);

      expect(find.byKey(const Key('motivo_de_merma')), findsNothing);
      expect(find.textContaining('catálogo de motivos'), findsOneWidget);
    });

    testWidgets('sin folios de merma lo dice al intentar', (tester) async {
      final base = await irALaMerma(tester, conFolios: false);
      final db = BaseLocalDePrueba(base);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '3');
      await tocar(tester, const Key('registrar_merma'));

      expect(db.contar('mermas'), 0);
      expect(find.textContaining('folios de merma'), findsOneWidget);
    });

    testWidgets('el rango por agotarse se avisa antes de que estorbe',
        (tester) async {
      await irALaMerma(tester, foliosHasta: 10, consumidoHasta: 8);
      expect(find.textContaining('folios de merma'), findsOneWidget);
    });

    testWidgets('después de guardar se dice qué significa en la liquidación',
        (tester) async {
      await irALaMerma(tester);

      await escoger(tester, const Key('motivo_de_merma'), 'Empaque roto');
      await capturar(tester, cuantas: '3');
      await tocar(tester, const Key('registrar_merma'));

      expect(find.textContaining('no va a aparecer como tuyo'), findsOneWidget);
      expect(find.byKey(const Key('merma_listo')), findsOneWidget);
    });

    testWidgets('la existencia del camión se muestra en cada renglón',
        (tester) async {
      await irALaMerma(tester, existencia: 240);
      await tocar(tester, const Key('escoger_p-sopa'));
      expect(find.textContaining('El camión trae'), findsOneWidget);
    });
  });

  group('la devolución', () {
    testWidgets('entra al camión en vez de salir', (tester) async {
      final base = await irALaDevolucion(tester);
      final db = BaseLocalDePrueba(base);

      await escoger(
        tester,
        const Key('motivo_de_merma'),
        'Devolución del cliente',
      );
      await capturar(tester, cuantas: '6');
      await tocar(tester, const Key('registrar_merma'));

      // 240 + 6 = 246: el signo contrario al de la merma.
      final camion = db.unaFila(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
      );
      expect(camion['cant_actual'], 246);
    });

    testWidgets('guarda al cliente que la devolvió', (tester) async {
      final base = await irALaDevolucion(tester);
      final db = BaseLocalDePrueba(base);

      await escoger(
        tester,
        const Key('motivo_de_merma'),
        'Devolución del cliente',
      );
      await capturar(tester, cuantas: '6');
      await tocar(tester, const Key('registrar_merma'));

      final merma = db.unaFila('SELECT tipo, cliente_id FROM mermas');
      expect(merma['tipo'], 'devolucion_cliente');
      expect(merma['cliente_id'], 'cliente-1');
    });

    testWidgets('abre en modo devolución cuando viene de la visita',
        (tester) async {
      await irALaDevolucion(tester);
      expect(find.text('Devolución del cliente'), findsWidgets);
    });

    testWidgets('sin cliente la pantalla dice de dónde se registra',
        (tester) async {
      await irALaMerma(tester);
      await tocar(tester, const Key('tipo_de_merma'));

      // Desde el camión no hay cliente, y una devolución sin él no se puede
      // revisar contra su venta.
      await tester.tap(find.text('Me la devolvió'));
      await tester.pumpAndSettle();
      expect(find.textContaining('desde la visita al cliente'), findsOneWidget);
    });
  });
}
