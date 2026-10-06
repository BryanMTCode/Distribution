/// Devolver mercancía del camión a la bodega, desde el teléfono.
///
/// Lo que estas pruebas defienden, y que no se ve probando solo el dominio:
///
/// · Que la pantalla **se alcance desde «Mi camión»**. La pregunta «¿qué bajo?» se
///   contesta mirando lo que trae; esconderla en un menú haría que nadie la use y
///   la devolución volvería a ser dos ajustes sueltos.
/// · Que el camión **baje al capturar**, no al recibir. Si no bajara, el teléfono
///   le seguiría ofreciendo al cliente mercancía que ya dejó en la bodega.
/// · Que la pantalla **no bloquee** por existencia (§0.1), igual que la merma.
/// · Que diga **«en tránsito»** con esas palabras. Si el vendedor cree que ya quedó
///   libre, se entera en la liquidación de que contaron 16 de las 18 que dejó.
/// · Que «todo el camión» cargue el SALDO, en unidad base. Capturar treinta
///   renglones a mano al final de la ruta es lo que hace que nadie lo capture.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra, abre «Mi camión» y de ahí la devolución a la bodega.
Future<BaseLocal> irADevolver(
  WidgetTester tester, {
  double existencia = 48,
}) async {
  final base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b, existenciaSopa: existencia);
      sembrarParaCobrar(b);
    },
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('boton_camion'));
  await tocar(tester, const Key('boton_devolver_a_bodega'));
  return base;
}

Future<void> capturar(
  WidgetTester tester, {
  String producto = 'p-sopa',
  String cuantas = '18',
  String? unidad,
}) async {
  await tocar(tester, Key('escoger_$producto'));
  if (unidad != null) {
    await tocar(tester, Key('unidad_$producto'));
    await tester.tap(find.text(unidad).last);
    await tester.pumpAndSettle();
  }
  await tester.enterText(find.byKey(Key('cuantas_$producto')), cuantas);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('se llega desde «Mi camión»', (tester) async {
    await irADevolver(tester);
    expect(find.text('Devolver a la bodega'), findsWidgets);
  });

  testWidgets('la mercancía sale del camión al capturar', (tester) async {
    final base = await irADevolver(tester);
    final db = BaseLocalDePrueba(base);

    await capturar(tester, cuantas: '18');
    await tocar(tester, const Key('registrar_traspaso'));

    expect(db.contar('traspasos'), 1);
    // 48 − 18 = 30. Si no bajara, el catálogo le ofrecería al cliente mercancía
    // que ya está en la bodega.
    final camion = db.unaFila(
      "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
    );
    expect(camion['cant_actual'], 30);
  });

  testWidgets('se captura en cajas y se guarda en piezas', (tester) async {
    final base = await irADevolver(tester);
    final db = BaseLocalDePrueba(base);

    await capturar(tester, cuantas: '2', unidad: 'CAJA');
    // La conversión se ve ANTES de guardar: es lo que atrapa el dedazo.
    expect(find.textContaining('48'), findsWidgets);

    await tocar(tester, const Key('registrar_traspaso'));

    final renglon = db.unaFila('SELECT cantidad_base FROM traspaso_detalle');
    expect(renglon['cantidad_base'], 48);
  });

  testWidgets('queda encolada para el servidor', (tester) async {
    final base = await irADevolver(tester);
    final db = BaseLocalDePrueba(base);

    await capturar(tester);
    await tocar(tester, const Key('registrar_traspaso'));

    final sobre = db.unaFila('SELECT tipo, payload FROM outbox');
    expect(sobre['tipo'], 'traspaso.crear');
    // Las cantidades viajan como texto de tres decimales: contracts §1.4.
    expect(sobre['payload'].toString(), contains('18.000'));
  });

  testWidgets('bajar más de lo que el camión dice se avisa y se permite',
      (tester) async {
    final base = await irADevolver(tester, existencia: 12);
    final db = BaseLocalDePrueba(base);

    await capturar(tester, cuantas: '18');
    expect(
      find.textContaining('más de lo que el camión dice'),
      findsOneWidget,
      reason: 'se avisa, no se bloquea: si lo bajó, lo bajó',
    );

    await tocar(tester, const Key('registrar_traspaso'));

    expect(db.contar('traspasos'), 1);
    // El renglón en negativo es la señal honesta.
    final camion = db.unaFila(
      "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
    );
    expect(camion['cant_actual'], -6);
  });

  testWidgets('sin ningún renglón no registra nada', (tester) async {
    final base = await irADevolver(tester);
    final db = BaseLocalDePrueba(base);

    await tocar(tester, const Key('registrar_traspaso'));

    expect(db.contar('traspasos'), 0);
    expect(find.textContaining('Escoge al menos un producto'), findsOneWidget);
  });

  testWidgets('dice que queda en tránsito, no que ya se entregó', (tester) async {
    await irADevolver(tester);

    // Antes de capturar: la advertencia de arriba.
    expect(find.textContaining('tránsito'), findsWidgets);

    await capturar(tester);
    await tocar(tester, const Key('registrar_traspaso'));

    // Y después de guardar, otra vez: es la diferencia entre «ya no es mío» y
    // «todavía nadie lo contó».
    expect(find.textContaining('tránsito'), findsWidgets);
    expect(find.textContaining('cuánto contaron'), findsOneWidget);
  });

  testWidgets('«todo el camión» carga el saldo en unidad base', (tester) async {
    final base = await irADevolver(tester, existencia: 50);
    final db = BaseLocalDePrueba(base);

    await tocar(tester, const Key('bajar_todo'));
    await tocar(tester, const Key('registrar_traspaso'));

    // 50 piezas no son dos cajas de 24: convertirlo a cajas perdería las dos
    // sueltas.
    final renglon = db.unaFila('SELECT cantidad_base FROM traspaso_detalle');
    expect(renglon['cantidad_base'], 50);
    final camion = db.unaFila(
      "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
    );
    expect(camion['cant_actual'], 0);
  });

  testWidgets('la lista muestra lo entregado y que nadie lo ha contado',
      (tester) async {
    await irADevolver(tester);

    await capturar(tester);
    await tocar(tester, const Key('registrar_traspaso'));
    await tocar(tester, const Key('traspaso_listo'));

    // De vuelta en «Mi camión»; se entra otra vez a ver el comprobante.
    await tocar(tester, const Key('boton_devolver_a_bodega'));

    expect(find.text('Sin folio todavía'), findsOneWidget);
    expect(
      find.textContaining('nadie la ha contado'),
      findsOneWidget,
      reason: 'es lo que distingue «entregado» de «recibido»',
    );
  });
}
