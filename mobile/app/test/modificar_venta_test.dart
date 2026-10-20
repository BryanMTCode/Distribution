/// Modificar una venta desde «Mi día»: subir, bajar o quitar (ADR 0002 §162).
///
/// «Que puedan quitar artículos de la venta, pero que se ajuste todo y el
/// inventario se devuelva a su camión; que puedan bajar la cantidad de un
/// artículo y subirla.» Lo que defienden estas pruebas:
/// · Lo que baja vuelve al camión, lo que sube sale, y el total se ajusta.
/// · Se puede agregar otro artículo, con el precio de la lista de la venta.
/// · No se sube más de lo que trae el camión; quitarlo todo es cancelar.
/// · Sin motivo no se guarda, y con «No» no pasa nada.
/// · El ticket nuevo dice «MODIFICADA».
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
const _pieza = 'p-sopa|PZA';

/// Termina la visita, abre «Mi día», despliega la venta y entra a modificarla.
Future<void> irAModificar(WidgetTester tester) async {
  await tocar(tester, const Key('boton_terminar_visita'));
  await tocar(tester, const Key('boton_mi_dia'));
  await tocar(tester, const Key('venta_VEND01-000001'));
  await tocar(tester, const Key('modificar_VEND01-000001'));
}

double enCamion(BaseLocal base) => BaseLocalDePrueba(base).unaFila(
      "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
    )['cant_actual'] as double;

bool habilitado(WidgetTester tester) =>
    tester.widget<FilledButton>(find.byKey(const Key('guardar_cambios'))).onPressed != null;

String cantidad(WidgetTester tester, String llave) =>
    tester.widget<Text>(find.byKey(Key('cantidad_$llave'))).data!;

Future<void> conMotivo(WidgetTester tester, String motivo) async {
  await traerAlArbol(tester, find.text(motivo));
  await tester.tap(find.text(motivo));
  await tester.pumpAndSettle();
}

Future<void> guardar(WidgetTester tester) async {
  await tocar(tester, const Key('guardar_cambios'));
  await tocar(tester, const Key('confirmar_guardar_cambios'));
}

void main() {
  late Directory temporal;

  setUp(() => temporal = Directory.systemTemp.createTempSync('tickets_modificada'));
  tearDown(() {
    if (temporal.existsSync()) temporal.deleteSync(recursive: true);
  });

  testWidgets('bajar: lo que baja vuelve al camión, y el ticket nuevo dice MODIFICADA',
      (tester) async {
    final impresora = ImpresoraSimulada(carpeta: temporal);
    final base = await cobrarDosCajas(
      tester,
      extras: [impresoraProvider.overrideWithValue(impresora)],
    );
    final db = BaseLocalDePrueba(base);
    expect(enCamion(base), 192.0);
    await irAModificar(tester);

    await tocar(tester, const Key('menos_$_caja'));
    expect(cantidad(tester, _caja), '1');
    expect(find.text('Total \$592.00 → \$296.00'), findsOneWidget);
    expect(habilitado(tester), isFalse, reason: 'sin motivo no se guarda');
    await conMotivo(tester, 'Me equivoqué de cantidad');
    expect(habilitado(tester), isTrue);
    await guardar(tester);

    expect(textoQueContiene('VEND01-000001 modificada'), findsOneWidget);
    final venta = db.unaFila('SELECT total, modificaciones FROM ventas');
    expect(venta['total'], 296.0);
    expect(venta['modificaciones'], 1);
    expect(enCamion(base), 216.0, reason: 'la caja que ya no se llevó volvió');
    expect(
      db.unaFila("SELECT COUNT(*) AS n FROM outbox WHERE tipo = 'venta.modificar'")['n'],
      1,
    );

    await tocarConProgreso(tester, const Key('imprimir_modificada'));
    final papel = decodificar(impresora.enviados.single).texto;
    expect(papel, contains('MODIFICADA'));
    expect(papel, contains('1 CAJA x 296.00'));
    expect(papel, isNot(contains('2 CAJA')));
  });

  testWidgets('subir y agregar otro artículo: sale del camión', (tester) async {
    final base = await cobrarDosCajas(tester);
    await irAModificar(tester);

    await tocar(tester, const Key('mas_$_caja'));
    await traerAlArbol(tester, find.byKey(const Key('agregar_otro_$_pieza')));
    await tocar(tester, const Key('agregar_otro_$_pieza'));
    await tocar(tester, const Key('mas_$_pieza'));
    expect(cantidad(tester, _caja), '3');
    expect(cantidad(tester, _pieza), '2');
    await conMotivo(tester, 'El cliente pidió más');
    await guardar(tester);

    // 240 − 3 cajas (72) − 2 piezas.
    expect(enCamion(base), 166.0);
    // 3 × 296 + 2 × 12.3333 = 888 + 24.67.
    expect(BaseLocalDePrueba(base).unaFila('SELECT total FROM ventas')['total'], 912.67);
  });

  testWidgets('quitarlo todo no se guarda: dice que se cancele', (tester) async {
    await cobrarDosCajas(tester);
    await irAModificar(tester);
    await tocar(tester, const Key('menos_$_caja'));
    await tocar(tester, const Key('menos_$_caja'));
    await conMotivo(tester, 'El cliente ya no quiso uno');

    expect(find.byKey(const Key('aviso_quitar_todo')), findsOneWidget);
    expect(habilitado(tester), isFalse);
  });

  testWidgets('no se sube más de lo que trae el camión', (tester) async {
    final base = await cobrarDosCajas(tester);
    await irAModificar(tester);

    // Quedan 192 piezas = 8 cajas; con las 2 de la venta caben 10.
    for (var i = 0; i < 9; i++) {
      await tocar(tester, const Key('mas_$_caja'));
    }
    expect(cantidad(tester, _caja), '10');
    expect(find.byKey(const Key('aviso_cambio')), findsOneWidget);
    expect(enCamion(base), 192.0, reason: 'nada se mueve hasta guardar');
  });

  testWidgets('con «No» en la confirmación no pasa nada', (tester) async {
    final base = await cobrarDosCajas(tester);
    await irAModificar(tester);
    await tocar(tester, const Key('menos_$_caja'));
    await conMotivo(tester, 'Me equivoqué de cantidad');
    await tocar(tester, const Key('guardar_cambios'));
    await tocar(tester, const Key('no_guardar_cambios'));

    expect(BaseLocalDePrueba(base).unaFila('SELECT total FROM ventas')['total'], 592.0);
    expect(enCamion(base), 192.0);
  });
}
