/// El vendedor cancela su venta desde «Mi día» (ADR 0002 §161).
///
/// «Quiero que los vendedores puedan cancelar ventas por si se equivocan.» Lo
/// que defienden estas pruebas:
/// · Con un motivo se cancela, sin señal: la mercancía vuelve al camión, sale del
///   efectivo y la tienda vuelve a la lista con su número.
/// · Sin motivo el botón no se habilita, y con «No» no pasa nada.
/// · Después del corte de ese día ya no se ofrece: la cancela la oficina.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';
import 'otra_venta_test.dart' show cobrarDosCajas;

/// Termina la visita, abre «Mi día» y despliega la venta.
Future<void> irAMiDia(WidgetTester tester) async {
  await tocar(tester, const Key('boton_terminar_visita'));
  await tocar(tester, const Key('boton_mi_dia'));
  await tocar(tester, const Key('venta_VEND01-000001'));
}

double enCamion(BaseLocal base) => BaseLocalDePrueba(base).unaFila(
      "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
    )['cant_actual'] as double;

void main() {
  testWidgets('con motivo se cancela: vuelve al camión, sale del efectivo y de «vendida»',
      (tester) async {
    final base = await cobrarDosCajas(tester);
    final db = BaseLocalDePrueba(base);
    expect(enCamion(base), 192.0);
    await irAMiDia(tester);

    await tocar(tester, const Key('cancelar_VEND01-000001'));
    expect(find.byKey(const Key('dialogo_cancelar_venta')), findsOneWidget);
    // Sin motivo no se puede.
    expect(
      tester
          .widget<FilledButton>(find.byKey(const Key('confirmar_cancelar_venta')))
          .onPressed,
      isNull,
    );
    await tester.tap(find.text('Me equivoqué de cliente'));
    await tester.pumpAndSettle();
    await tocar(tester, const Key('confirmar_cancelar_venta'));

    expect(textoQueContiene('VEND01-000001 cancelada'), findsOneWidget);
    expect(db.unaFila('SELECT estado, nota_oficina FROM ventas')['estado'], 'cancelada');
    expect(db.unaFila('SELECT nota_oficina FROM ventas')['nota_oficina'],
        'Me equivoqué de cliente');
    expect(enCamion(base), 240.0);
    expect(
      db.unaFila("SELECT COUNT(*) AS n FROM outbox WHERE tipo = 'venta.cancelar'")['n'],
      1,
    );
    // Sale del efectivo de «Mi día», y aparece entre las canceladas.
    await traerAlArbol(tester, find.byKey(const Key('monto_efectivo')));
    expect(
      tester.widget<Text>(find.byKey(const Key('monto_efectivo'))).data,
      '\$0.00',
    );
    await traerAlArbol(tester, find.byKey(const Key('tarjeta_oficina')));
    expect(find.byKey(const Key('tarjeta_oficina')), findsOneWidget);

    // Y la tienda vuelve a la lista con su número (§156).
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('vendida_cliente-1')), findsNothing);
  });

  testWidgets('con «No» no pasa nada', (tester) async {
    final base = await cobrarDosCajas(tester);
    await irAMiDia(tester);

    await tocar(tester, const Key('cancelar_VEND01-000001'));
    await tocar(tester, const Key('no_cancelar_venta'));

    expect(BaseLocalDePrueba(base).unaFila('SELECT estado FROM ventas')['estado'],
        'confirmada');
    expect(enCamion(base), 192.0);
  });

  testWidgets('después del corte de ese día ya no se ofrece', (tester) async {
    final base = await cobrarDosCajas(tester);
    base.db.execute(
      "INSERT INTO cortes_vendedor (id, fecha_operativa, efectivo_declarado, "
      "fecha_dispositivo) SELECT 'corte-1', fecha_operativa, 592, creado_en FROM ventas",
    );
    await irAMiDia(tester);

    expect(find.byKey(const Key('cancelar_VEND01-000001')), findsNothing);
    expect(find.byKey(const Key('cancelar_tras_corte_VEND01-000001')), findsOneWidget);
    expect(find.byKey(const Key('reimprimir_VEND01-000001')), findsOneWidget);
  });

  testWidgets('la de un día que ya pasó tampoco: la cancela la oficina', (tester) async {
    final base = await cobrarDosCajas(tester);
    base.db.execute("UPDATE ventas SET fecha_operativa = date(fecha_operativa, '-1 day')");
    await tocar(tester, const Key('boton_terminar_visita'));
    await tocar(tester, const Key('boton_mi_dia'));
    await tocar(tester, const Key('boton_dia_anterior'));
    await tocar(tester, const Key('venta_VEND01-000001'));

    expect(find.byKey(const Key('cancelar_VEND01-000001')), findsNothing);
    expect(textoQueContiene('Es de un día que ya pasó'), findsOneWidget);
  });
}
