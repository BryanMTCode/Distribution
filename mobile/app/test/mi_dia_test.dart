/// «Mi día» en la pantalla.
///
/// Lo que se prueba aquí no es la aritmética —eso lo cubre `mi_dia_test.dart` de
/// dsd_core— sino que el vendedor vea el número correcto y que la transferencia
/// NO se presente como dinero en su bolsa. Un desglose que invita a sumar mal es
/// peor que no tenerlo.
library;

import 'package:flutter/material.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/datos/base_local.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  Future<void> abrirMiDia(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('boton_mi_dia')));
    await tester.pumpAndSettle();
  }

  testWidgets('el efectivo es la suma de las ventas en efectivo', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'V1', total: 120.00);
        sembrarVentaDelDia(base, folio: 'V2', total: 80.00);
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    final monto = tester.widget<Text>(find.byKey(const Key('monto_efectivo')));
    expect(monto.data, equals('\$200.00'));
  });

  testWidgets('la transferencia no se cuenta como efectivo', (tester) async {
    // La regla que más cuesta en la calle: se vendió y el dinero no está en la
    // bolsa, está en el banco.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'V1', total: 100.00);
        sembrarVentaDelDia(
          base,
          folio: 'V2',
          total: 900.00,
          formaPago: 'transferencia',
        );
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    final monto = tester.widget<Text>(find.byKey(const Key('monto_efectivo')));
    expect(monto.data, equals('\$100.00'),
        reason: 'con la transferencia sumada el vendedor creería que le falta '
            'dinero en la caja, y la oficina le va a pedir 100');
    // Y la transferencia se ve, para que sepa que está ahí y que no la trae.
    expect(textoQueContiene('llegó al banco, no a tu bolsa'), findsOneWidget);
    expect(textoQueContiene('\$1000.00'), findsWidgets, reason: 'el total vendido');
    expect(textoQueContiene('rédito'), findsNothing);
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

  testWidgets('al tocar una venta se despliega lo que se le vendió', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarProducto(base, id: 'p-sopa', nombre: 'Sopa de fideo 70 g');
        sembrarVentaDelDia(base, folio: 'V1', total: 592.00);
        base.db.execute(
          'INSERT INTO venta_partidas (id, venta_id, linea, producto_id, unidad_codigo, '
          '       factor_unidad, cantidad, cantidad_base, precio_unitario, importe) '
          "VALUES ('pa-1', 'V1', 1, 'p-sopa', 'CAJA', 24, 2, 48, 296, 592)",
        );
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    expect(textoQueContiene('Sopa de fideo'), findsNothing,
        reason: 'cerrada, la lista se lee de un vistazo');
    await tester.tap(find.byKey(const Key('venta_V1')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('2 CAJA · Sopa de fideo 70 g'), findsOneWidget);
    expect(textoQueContiene('592.00'), findsWidgets);
  });

  testWidgets('al sincronizar, la venta pasa a «subida» sin reiniciar la app',
      (tester) async {
    // Bug de campo, octubre 2026: se quedaba en rojo hasta cerrar y abrir la app.
    late BaseLocal laBase;
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        laBase = base;
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'V1', total: 10.00);
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);
    Finder icono(IconData i) =>
        find.descendant(of: find.byKey(const Key('venta_V1')), matching: find.byIcon(i));
    expect(icono(Icons.cloud_off_outlined), findsOneWidget);

    // Lo que hace la sincronización al terminar: el documento queda marcado y
    // se avisa a la cola.
    laBase.db.execute("UPDATE ventas SET sincronizada = 1 WHERE id = 'V1'");
    ProviderScope.containerOf(tester.element(find.byKey(const Key('venta_V1'))))
        .invalidate(resumenColaProvider);
    await tester.pumpAndSettle();

    expect(icono(Icons.cloud_done_outlined), findsOneWidget);
    expect(find.byKey(const Key('aviso_sin_sincronizar')), findsNothing);
  });

  testWidgets('un día sin ventas lo dice en lugar de mostrar ceros sueltos',
      (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    expect(textoQueContiene('Todavía no has vendido hoy'), findsOneWidget);
  });

  group('lo que la oficina cambió', () {
    testWidgets('APARECE CON SU MOTIVO, y ya está descontado', (tester) async {
      // Sin esto, el vendedor ve su efectivo bajar sin explicación y concluye que
      // la app le perdió una venta. Al día siguiente apunta en papel «por si
      // acaso», y ahí se pierde el sistema.
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
          sembrarVentaDelDia(base, folio: 'V1', total: 500.00);
          sembrarVentaDelDia(
            base,
            folio: 'V2',
            total: 300.00,
            estado: 'cancelada',
            notaOficina: 'se facturó al cliente equivocado',
          );
        },
      );
      await entrarCon(tester, pinCorrecto);
      await abrirMiDia(tester);

      // El efectivo ya NO incluye la cancelada.
      expect(textoDe(tester, const Key('monto_efectivo')), equals('\$500.00'));

      expect(find.byKey(const Key('tarjeta_oficina')), findsOneWidget);
      expect(find.byKey(const Key('oficina_V2')), findsOneWidget);
      expect(textoQueContiene('se facturó al cliente equivocado'), findsOneWidget);
      expect(textoQueContiene('La mercancía volvió a tu camión'), findsOneWidget);
    });

    testWidgets('un día que la oficina no tocó no muestra la tarjeta', (tester) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
          sembrarVentaDelDia(base, folio: 'V1', total: 500.00);
        },
      );
      await entrarCon(tester, pinCorrecto);
      await abrirMiDia(tester);

      expect(find.byKey(const Key('tarjeta_oficina')), findsNothing);
    });
  });

  testWidgets('el vendedor revisa días anteriores y vuelve a hoy', (tester) async {
    // Pedido en operación: «que en Mi día lo deje cambiar de día para revisar
    // días anteriores». Los documentos de ayer siguen en el teléfono.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarVentaDelDia(base, folio: 'HOY1', total: 100.00);
        sembrarVentaDelDia(base, folio: 'AYER1', total: 345.00,
            fechaOperativa: '2026-09-23');
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);

    expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
    expect(find.byKey(const Key('aviso_otro_dia')), findsNothing);
    // Hoy no se puede ir a mañana.
    expect(
      tester.widget<IconButton>(find.byKey(const Key('boton_dia_siguiente'))).onPressed,
      isNull,
    );

    await tester.tap(find.byKey(const Key('boton_dia_anterior')));
    await tester.pumpAndSettle();

    expect(find.text('Ayer, miércoles 23 de septiembre'), findsOneWidget);
    expect(find.byKey(const Key('aviso_otro_dia')), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const Key('monto_efectivo'))).data, '\$345.00');
    expect(find.text('Efectivo de ese día'), findsOneWidget);
    await tester.scrollUntilVisible(find.byKey(const Key('venta_AYER1')), 200);
    expect(find.byKey(const Key('venta_AYER1')), findsOneWidget);
    expect(find.byKey(const Key('venta_HOY1')), findsNothing);

    // Y de vuelta a hoy, con la flecha.
    await tester.scrollUntilVisible(find.byKey(const Key('monto_efectivo')), -200);
    await tester.tap(find.byKey(const Key('boton_dia_siguiente')));
    await tester.pumpAndSettle();
    expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const Key('monto_efectivo'))).data, '\$100.00');
  });

  testWidgets('salir de Mi día y volver a entrar abre otra vez en hoy', (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinCorrecto);
    await abrirMiDia(tester);
    await tester.tap(find.byKey(const Key('boton_dia_anterior')));
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();

    await abrirMiDia(tester);
    expect(find.text('Hoy, jueves 24 de septiembre'), findsOneWidget);
  });
}
