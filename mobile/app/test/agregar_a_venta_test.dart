/// Agregarle a una venta lo que le faltó, desde «Mi día» (ADR 0002 §162).
///
/// «Si les faltó poner uno de un artículo, que lo agreguen y puedan imprimir el
/// nuevo ticket.» Lo que defienden estas pruebas:
/// · Lo agregado sale del camión, sube el total y viaja por la cola.
/// · No se agrega más de lo que trae el camión.
/// · Sin motivo no se guarda.
/// · El ticket nuevo dice «MODIFICADA», con lo de antes y lo agregado.
library;

import 'dart:io';

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/impresora.dart';
import 'package:dsd_app/src/estado/carrito.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';
import 'otra_venta_test.dart' show cobrarDosCajas;

const _caja = 'p-sopa|CAJA';

/// Termina la visita, abre «Mi día», despliega la venta y entra a agregar.
Future<void> irAAgregar(WidgetTester tester) async {
  await tocar(tester, const Key('boton_terminar_visita'));
  await tocar(tester, const Key('boton_mi_dia'));
  await tocar(tester, const Key('venta_VEND01-000001'));
  await tocar(tester, const Key('agregar_VEND01-000001'));
}

double enCamion(BaseLocal base) => BaseLocalDePrueba(base).unaFila(
      "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
    )['cant_actual'] as double;

bool habilitado(WidgetTester tester) =>
    tester.widget<FilledButton>(find.byKey(const Key('guardar_agregado'))).onPressed != null;

void main() {
  late Directory temporal;

  setUp(() => temporal = Directory.systemTemp.createTempSync('tickets_agregado'));
  tearDown(() {
    if (temporal.existsSync()) temporal.deleteSync(recursive: true);
  });

  testWidgets('agrega lo que le faltó, sale del camión, y el ticket nuevo dice MODIFICADA',
      (tester) async {
    final impresora = ImpresoraSimulada(carpeta: temporal);
    final base = await cobrarDosCajas(
      tester,
      extras: [impresoraProvider.overrideWithValue(impresora)],
    );
    final db = BaseLocalDePrueba(base);
    await irAAgregar(tester);

    await tocar(tester, const Key('mas_agregar_$_caja'));
    expect(find.text('Se agrega \$296.00 · total nuevo \$888.00'), findsOneWidget);
    expect(habilitado(tester), isFalse, reason: 'sin motivo no se guarda');
    await tester.tap(find.text('Se me olvidó agregarlo'));
    await tester.pumpAndSettle();
    expect(habilitado(tester), isTrue);

    await tocar(tester, const Key('guardar_agregado'));
    await tocar(tester, const Key('confirmar_agregar'));

    expect(textoQueContiene('VEND01-000001 modificada'), findsOneWidget);
    final venta = db.unaFila('SELECT total, modificaciones FROM ventas');
    expect(venta['total'], 888.0);
    expect(venta['modificaciones'], 1);
    expect(enCamion(base), 168.0, reason: '240 − 48 − 24');
    expect(
      db.unaFila("SELECT COUNT(*) AS n FROM outbox WHERE tipo = 'venta.agregar'")['n'],
      1,
    );

    // El ticket nuevo, desde el mismo aviso.
    await tocarConProgreso(tester, const Key('imprimir_modificada'));
    expect(impresora.enviados, hasLength(1));
    final papel = decodificar(impresora.enviados.single).texto;
    expect(papel, contains('MODIFICADA'));
    expect(papel, contains('2 CAJA x 296.00'));
    expect(papel, contains('1 CAJA x 296.00'));
    expect(papel, contains('888.00'));
  });

  testWidgets('no se agrega más de lo que trae el camión', (tester) async {
    final base = await cobrarDosCajas(tester);
    await irAAgregar(tester);

    // Quedan 192 piezas: 8 cajas.
    for (var i = 0; i < 9; i++) {
      await tocar(tester, const Key('mas_agregar_$_caja'));
    }
    expect(
      tester.widget<Text>(find.byKey(const Key('cantidad_agregar_$_caja'))).data,
      '8',
    );
    expect(find.byKey(const Key('aviso_agregado')), findsOneWidget);
    expect(enCamion(base), 192.0, reason: 'nada se mueve hasta guardar');
  });

  testWidgets('con «No» en la confirmación no se agrega nada', (tester) async {
    final base = await cobrarDosCajas(tester);
    await irAAgregar(tester);
    await tocar(tester, const Key('mas_agregar_$_caja'));
    await tester.tap(find.text('El cliente pidió más'));
    await tester.pumpAndSettle();
    await tocar(tester, const Key('guardar_agregado'));
    await tocar(tester, const Key('no_agregar'));

    expect(BaseLocalDePrueba(base).unaFila('SELECT total FROM ventas')['total'], 592.0);
    expect(enCamion(base), 192.0);
  });
}
