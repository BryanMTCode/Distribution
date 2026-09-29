/// La pantalla del pedido.
///
/// Lo que se cuida aquí es lo que el cliente va a revisar en el papel impreso:
/// que la aritmética de cada renglón esté a la vista, que el total cuadre con la
/// suma de los renglones, y que el crédito diga **el número que resuelve la
/// situación** ("necesita abonar $192.00") en vez de "operación no permitida".
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra, abre la visita, agrega y va al pedido.
Future<void> abrirPedido(
  WidgetTester tester, {
  int cajas = 1,
  int piezas = 0,
  bool permiteCredito = true,
  double limite = 5000,
  double saldoCache = 0,
  bool bloqueado = false,
  void Function(BaseLocal base)? sembrarExtra,
}) async {
  await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (base) {
      sembrarEscenarioDeVenta(
        base,
        permiteCredito: permiteCredito,
        limite: limite,
        saldoCache: saldoCache,
        bloqueado: bloqueado,
      );
      sembrarExtra?.call(base);
    },
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));

  for (var i = 0; i < cajas; i++) {
    await tocar(
      tester,
      i == 0 ? const Key('agregar_p-sopa|CAJA') : const Key('mas_p-sopa|CAJA'),
    );
  }
  for (var i = 0; i < piezas; i++) {
    // El primer toque usa el botón de agregar; a partir del segundo, el
    // contador ya reemplazó al botón.
    await tocar(
      tester,
      i == 0 ? const Key('agregar_p-sopa|PZA') : const Key('mas_p-sopa|PZA'),
    );
  }
  await tocar(tester, const Key('boton_ver_carrito'));
}

void main() {
  group('la aritmética a la vista', () {
    testWidgets('cada renglón muestra cantidad × precio = importe',
        (tester) async {
      await abrirPedido(tester, cajas: 2);

      // Es lo que el cliente revisa en el papel. Si no cuadra en pantalla, el
      // vendedor lo descubre AHORA y no con el ticket ya impreso.
      expect(find.text('2 CAJA × \$296.00'), findsOneWidget);
      expect(
        textoDe(tester, const Key('importe_p-sopa|CAJA')),
        equals('\$592.00'),
      );
    });

    testWidgets('dice cuántas piezas salen del camión', (tester) async {
      await abrirPedido(tester, cajas: 3);
      // Sin esto, "3 cajas" no le dice al vendedor que se lleva 72 piezas.
      expect(textoQueContiene('Salen 72 pza del camión'), findsOneWidget);
    });

    testWidgets('el total es la suma de los renglones impresos',
        (tester) async {
      // Una caja (296.00) más una pieza (12.3333 → 12.33). El total tiene que
      // ser 308.33: la suma de lo que dicen los renglones, no el redondeo de
      // 308.3333.
      await abrirPedido(tester, cajas: 1, piezas: 1);

      expect(
        textoDe(tester, const Key('importe_p-sopa|PZA')),
        equals('\$12.33'),
      );
      expect(
        textoDe(tester, const Key('total_pedido')),
        equals('\$308.33'),
      );
    });

    testWidgets('caja y pieza son dos renglones', (tester) async {
      // El cliente pide "dos cajas y tres piezas": así se lo lleva y así se
      // imprime.
      await abrirPedido(tester, cajas: 2, piezas: 3);
      expect(find.byKey(const Key('importe_p-sopa|CAJA')), findsOneWidget);
      expect(find.byKey(const Key('importe_p-sopa|PZA')), findsOneWidget);
    });
  });

  group('editar el pedido', () {
    testWidgets('subir y bajar la cantidad recalcula el total', (tester) async {
      await abrirPedido(tester, cajas: 1);
      await tocar(tester, const Key('linea_mas_p-sopa|CAJA'));
      expect(
        textoDe(tester, const Key('total_pedido')),
        equals('\$592.00'),
      );

      await tocar(tester, const Key('linea_menos_p-sopa|CAJA'));
      expect(
        textoDe(tester, const Key('total_pedido')),
        equals('\$296.00'),
      );
    });

    testWidgets('quitar el último renglón deja el pedido vacío',
        (tester) async {
      await abrirPedido(tester, cajas: 1);
      await tocar(tester, const Key('linea_quitar_p-sopa|CAJA'));
      expect(find.byKey(const Key('pedido_vacio')), findsOneWidget);
    });

    testWidgets('vaciar pide confirmación', (tester) async {
      // Perder un pedido de quince renglones armado frente al cliente es rehacer
      // la visita completa.
      await abrirPedido(tester, cajas: 2);
      await tocar(tester, const Key('boton_vaciar'));

      expect(find.text('¿Vaciar el pedido?'), findsOneWidget);
      await tocar(tester, const Key('confirmar_vaciar'));
      expect(find.byKey(const Key('pedido_vacio')), findsOneWidget);
    });

    testWidgets('cancelar el vaciado no toca nada', (tester) async {
      await abrirPedido(tester, cajas: 2);
      await tocar(tester, const Key('boton_vaciar'));
      await tester.tap(find.text('Cancelar'));
      await tester.pumpAndSettle();

      expect(
        textoDe(tester, const Key('total_pedido')),
        equals('\$592.00'),
      );
    });
  });

  group('la forma de pago', () {
    testWidgets('arranca en contado', (tester) async {
      await abrirPedido(tester, cajas: 1);
      expect(find.byKey(const Key('forma_de_pago')), findsOneWidget);
      // De contado no hay nada que evaluar: procede siempre.
      expect(find.byKey(const Key('credito_ok')), findsNothing);
      expect(find.byKey(const Key('credito_bloquea')), findsNothing);
    });

    testWidgets('a crédito con línea suficiente dice cuánto le queda',
        (tester) async {
      await abrirPedido(tester, cajas: 1, limite: 5000);
      await tocar(tester, const Key('forma_de_pago'));
      await tester.tap(find.text('Crédito'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('credito_ok')), findsOneWidget);
      // 5000 - 296 = 4704.
      expect(textoQueContiene('\$4704.00'), findsOneWidget);
    });

    testWidgets('al pasarse del límite dice CUÁNTO FALTA ABONAR',
        (tester) async {
      // Límite 500, saldo 100, una caja de 296... no se pasa. Con dos sí:
      // 100 + 592 = 692 sobre 500 ⇒ faltan 192.
      await abrirPedido(tester, cajas: 2, limite: 500, saldoCache: 100);
      await tester.tap(find.text('Crédito'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('credito_bloquea')), findsOneWidget);
      // El número que resuelve la situación. "No permitido" deja al vendedor sin
      // salida frente al cliente.
      expect(textoQueContiene('abonar \$192.00'), findsOneWidget);
      // Y le ofrece la salida que sí existe.
      expect(textoQueContiene('de contado'), findsOneWidget);
    });

    testWidgets('un cliente bloqueado no puede a crédito, pero sí de contado',
        (tester) async {
      await abrirPedido(tester, cajas: 1, bloqueado: true);
      await tester.tap(find.text('Crédito'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('credito_bloquea')), findsOneWidget);
      expect(textoQueContiene('bloqueó su crédito'), findsOneWidget);

      // De contado la venta procede: negarla no cobra la deuda vieja y sí
      // pierde la venta nueva.
      await tester.tap(find.text('Contado'));
      await tester.pumpAndSettle();
      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('boton_revisar_venta')),
      );
      expect(boton.onPressed, isNotNull);
    });

    testWidgets('un cliente de solo contado no puede ni elegir crédito',
        (tester) async {
      // Ofrecer una opción que va a fallar es hacerle perder tiempo frente al
      // cliente.
      await abrirPedido(tester, cajas: 1, permiteCredito: false);
      expect(find.byKey(const Key('nota_solo_contado')), findsOneWidget);

      final segmentos = tester.widget<SegmentedButton<bool>>(
        find.byKey(const Key('forma_de_pago')),
      );
      final credito = segmentos.segments.firstWhere((s) => s.value == true);
      expect(credito.enabled, isFalse);
    });

    testWidgets('una venta a crédito encolada ya bajó el disponible',
        (tester) async {
      // Límite 3000, saldo 900, y una venta de 1500 que aún no sincroniza.
      // Disponible real: 600. Una caja de 296 pasa; el aviso lo confirma.
      await abrirPedido(
        tester,
        cajas: 1,
        limite: 3000,
        saldoCache: 900,
        sembrarExtra: (base) => sembrarVentaACreditoPendiente(
          base,
          clienteId: 'cliente-1',
          total: 1500,
        ),
      );
      await tester.tap(find.text('Crédito'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('credito_ok')), findsOneWidget);
      // 3000 - 900 - 1500 - 296 = 304.
      expect(textoQueContiene('\$304.00'), findsOneWidget);
    });
  });

  group('el paso siguiente', () {
    testWidgets('el botón no miente: dice "Revisar venta", no "Cobrar"',
        (tester) async {
      await abrirPedido(tester, cajas: 1);
      expect(find.text('Revisar venta'), findsOneWidget);
      expect(find.text('Cobrar'), findsNothing);
    });

    testWidgets('el resumen muestra los números y dice qué falta',
        (tester) async {
      await abrirPedido(tester, cajas: 2);
      await tocar(tester, const Key('boton_revisar_venta'));

      expect(find.byKey(const Key('resumen_venta')), findsOneWidget);
      expect(find.text('Abarrotes Doña Mary'), findsWidgets);
      expect(find.text('Contado'), findsWidgets);
      expect(textoQueContiene('todavía no se guarda'), findsOneWidget);
    });

    testWidgets('con el crédito bloqueado el botón está muerto',
        (tester) async {
      await abrirPedido(tester, cajas: 2, limite: 500, saldoCache: 100);
      await tester.tap(find.text('Crédito'));
      await tester.pumpAndSettle();

      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('boton_revisar_venta')),
      );
      expect(boton.onPressed, isNull);
    });
  });

  group('lo que NO existe en esta pantalla', () {
    testWidgets('no hay campo de precio ni de descuento', (tester) async {
      // La regla no está deshabilitada: no existe (ADR 0002 §7). Si alguien
      // agregara un campo editable, esta prueba lo atrapa.
      await abrirPedido(tester, cajas: 2);

      // Solo debe haber campos de texto en el catálogo (búsqueda), no aquí.
      expect(find.byType(TextField), findsNothing);
      expect(textoQueContiene('Descuento'), findsNothing);
      expect(textoQueContiene('descuento'), findsNothing);
    });
  });
}
