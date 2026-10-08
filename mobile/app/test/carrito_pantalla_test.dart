/// La pantalla del pedido.
///
/// Lo que se cuida aquí es lo que el cliente va a revisar en el papel impreso:
/// que la aritmética de cada renglón esté a la vista, que el total cuadre con la
/// suma de los renglones, y que la forma de pago diga a dónde va el dinero:
/// todo es de contado (ADR 0002 §81), en efectivo o por transferencia.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra, abre la visita, agrega y va al pedido.
Future<BaseLocal> abrirPedido(
  WidgetTester tester, {
  int cajas = 1,
  int piezas = 0,
  void Function(BaseLocal base)? sembrarExtra,
}) async {
  final base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (base) {
      sembrarEscenarioDeVenta(base);
      sembrarParaCobrar(base);
      sembrarExtra?.call(base);
    },
    extras: [
      servicioUbicacionProvider.overrideWithValue(
        ServicioUbicacionFalso.siempre(const GpsSinLectura()),
      ),
    ],
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
  return base;
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
    testWidgets('arranca en efectivo y no habla de crédito', (tester) async {
      await abrirPedido(tester, cajas: 1);
      final segmentos = tester.widget<SegmentedButton<FormaDePago>>(
        find.byKey(const Key('forma_de_pago')),
      );
      expect(segmentos.selected, {FormaDePago.efectivo});
      // Todo es de contado (ADR 0002 §81): no existe la opción.
      expect(segmentos.segments.map((s) => s.value),
          [FormaDePago.efectivo, FormaDePago.transferencia]);
      expect(textoQueContiene('rédito'), findsNothing);
      expect(find.byKey(const Key('campo_referencia_pago')), findsNothing);
    });

    testWidgets('por transferencia pide la referencia y avisa que no va al corte',
        (tester) async {
      await abrirPedido(tester, cajas: 1);
      await tester.tap(find.text('Transferencia'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('campo_referencia_pago')), findsOneWidget);
      expect(textoQueContiene('No viene en tu efectivo del corte'), findsOneWidget);

      await tester.tap(find.text('Efectivo'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('campo_referencia_pago')), findsNothing);
    });
  });

  group('el botón de cobrar', () {
    testWidgets('dice cómo se va a cobrar', (tester) async {
      await abrirPedido(tester, cajas: 1);
      expect(find.text('Cobrar en efectivo'), findsOneWidget);

      await tester.tap(find.text('Transferencia'));
      await tester.pumpAndSettle();
      expect(find.text('Cobrar por transferencia'), findsOneWidget);
    });

    testWidgets('está vivo con cualquier forma de pago: no hay límite que negar',
        (tester) async {
      await abrirPedido(tester, cajas: 2);
      for (final forma in ['Efectivo', 'Transferencia']) {
        await tester.tap(find.text(forma));
        await tester.pumpAndSettle();
        final boton = tester.widget<ButtonStyleButton>(
          find.byKey(const Key('boton_cobrar')),
        );
        expect(boton.onPressed, isNotNull, reason: forma);
      }
    });

    testWidgets('la venta por transferencia viaja con su forma y su referencia',
        (tester) async {
      final base = await abrirPedido(tester, cajas: 1);
      await tester.tap(find.text('Transferencia'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('campo_referencia_pago')),
        'SPEI 4471',
      );
      await tester.pumpAndSettle();
      await tocar(tester, const Key('boton_cobrar'));

      final venta = base.db
          .select('SELECT tipo, forma_pago, referencia_pago FROM ventas')
          .single;
      expect(venta['tipo'], 'contado');
      expect(venta['forma_pago'], 'transferencia');
      expect(venta['referencia_pago'], 'SPEI 4471');
      final sobre = base.db
          .select("SELECT payload FROM outbox WHERE tipo = 'venta.crear'")
          .single['payload'] as String;
      expect(sobre, contains('"forma_pago":"transferencia"'));
      expect(sobre, contains('"referencia_pago":"SPEI 4471"'));
    });
  });

  group('lo que NO existe en esta pantalla', () {
    testWidgets('no hay campo de precio ni de descuento', (tester) async {
      // La regla no está deshabilitada: no existe (ADR 0002 §7). Si alguien
      // agregara un campo editable, esta prueba lo atrapa.
      await abrirPedido(tester, cajas: 2);

      // Solo debe haber campos de texto en el catálogo (búsqueda), no aquí. La
      // referencia de la transferencia solo aparece al elegirla.
      expect(find.byType(TextField), findsNothing);
      expect(textoQueContiene('Descuento'), findsNothing);
      expect(textoQueContiene('descuento'), findsNothing);
    });
  });
}
