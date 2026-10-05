/// «Mi día» en la pantalla.
///
/// Lo que se prueba aquí no es la aritmética —eso lo cubre `mi_dia_test.dart` de
/// dsd_core— sino que el vendedor vea el número correcto y que el crédito NO se
/// presente como dinero. Un desglose que invita a sumar mal es peor que no tenerlo.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  Future<void> abrirMiDia(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('boton_mi_dia')));
    await tester.pumpAndSettle();
  }

  testWidgets('el efectivo es contado más cobros en efectivo', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'V1', total: 120.00);
        sembrarVentaDelDia(base, folio: 'V2', total: 80.00);
        sembrarCobroDelDia(base, folio: 'C1', importe: 50.00);
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    expect(find.byKey(const Key('monto_efectivo')), findsOneWidget);
    expect(textoQueContiene('250.00'), findsWidgets,
        reason: '120 + 80 de contado más 50 de cobro en efectivo');
  });

  testWidgets('el crédito no se cuenta como efectivo', (tester) async {
    // La regla que más cuesta en la calle: salió mercancía y no entró dinero.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'V1', total: 100.00);
        sembrarVentaDelDia(base, folio: 'V2', total: 900.00, tipo: 'credito');
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    final monto = tester.widget<Text>(find.byKey(const Key('monto_efectivo')));
    expect(monto.data, equals('\$100.00'),
        reason: 'con el crédito sumado el vendedor creería que le falta dinero '
            'en la caja, y la oficina le va a pedir 100');
    // Y el crédito se ve, para que sepa que está ahí y que no es dinero.
    expect(textoQueContiene('NO entró dinero'), findsOneWidget);
  });

  testWidgets('avisa de lo que todavía no ha subido', (tester) async {
    // Es lo que tiene que ver ANTES de entregar: lo que no subió, la oficina
    // todavía no lo sabe.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'V1', total: 10.00);
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    expect(find.byKey(const Key('aviso_sin_sincronizar')), findsOneWidget);
  });

  testWidgets('un día sin ventas lo dice en lugar de mostrar ceros sueltos',
      (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    expect(textoQueContiene('Todavía no has vendido hoy'), findsOneWidget);
  });
}
