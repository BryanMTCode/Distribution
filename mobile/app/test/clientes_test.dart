/// Lista de clientes de la ruta.
///
/// La prueba que cierra el círculo del proyecto es
/// `una venta a crédito sin sincronizar baja el disponible que ve el vendedor`:
/// la regla de `dsd_core/credito.dart` compuesta con la cola local de SQLite,
/// llegando hasta el pixel.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  Future<void> entrar(WidgetTester tester, void Function(dynamic base) sembrar) async {
    await montarApp(tester, credencial: credencialDelServidor(), sembrar: sembrar);
    await entrarCon(tester, pinCorrecto);
  }

  testWidgets('los clientes se muestran en orden de visita', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c3', nombre: 'Tercera', secuencia: 3);
      sembrarCliente(base, id: 'c1', nombre: 'Primera', secuencia: 1);
      sembrarCliente(base, id: 'c2', nombre: 'Segunda', secuencia: 2);
    });

    final nombres = tester
        .widgetList<ListTile>(find.byType(ListTile))
        .map((t) => ((t.title as Row).children.first as Expanded).child)
        .map((w) => (w as Text).data)
        .toList();
    expect(nombres, equals(['Primera', 'Segunda', 'Tercera']));
  });

  testWidgets('la búsqueda filtra sin señal', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Abarrotes Doña Mary');
      sembrarCliente(base, id: 'c2', nombre: 'La Esquina de Ñoño');
    });

    await tester.enterText(find.byKey(const Key('campo_busqueda')), 'Ñoño');
    await tester.pumpAndSettle();

    expect(find.text('La Esquina de Ñoño'), findsOneWidget);
    expect(find.text('Abarrotes Doña Mary'), findsNothing);
  });

  testWidgets('una búsqueda sin resultados lo dice', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Abarrotes Doña Mary');
    });
    await tester.enterText(find.byKey(const Key('campo_busqueda')), 'zzzz');
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('lista_vacia')), findsOneWidget);
  });

  // -------------------------------------------------------------------------
  // Crédito en pantalla
  // -------------------------------------------------------------------------

  testWidgets('muestra el disponible de quien tiene línea', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Con crédito', limite: 5000, saldoCache: 1200);
    });
    expect(find.text('\$3800.00'), findsOneWidget);
  });

  testWidgets('un cliente sin línea se marca como contado', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Solo contado', permiteCredito: false);
    });
    expect(find.byKey(const Key('credito_solo_contado')), findsOneWidget);
  });

  testWidgets('un cliente bloqueado se marca como bloqueado', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Bloqueado', bloqueado: true);
    });
    expect(find.byKey(const Key('credito_bloqueado')), findsOneWidget);
  });

  testWidgets('con el límite agotado se marca sin crédito', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Agotado', limite: 1000, saldoCache: 1000);
    });
    expect(find.byKey(const Key('credito_agotado')), findsOneWidget);
  });

  testWidgets('una venta a crédito sin sincronizar baja el disponible que ve el vendedor',
      (tester) async {
    // LA prueba del proyecto. Sin contar la cola local, la pantalla diría
    // \$5000 disponibles y el vendedor le seguiría vendiendo a crédito a un
    // cliente que ya se pasó — porque ninguna venta de la mañana alcanzó a
    // sincronizar.
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary', limite: 5000, saldoCache: 0);
      sembrarVentaACreditoPendiente(base, clienteId: 'c1', total: 1500);
    });

    expect(find.text('\$3500.00'), findsOneWidget);
    expect(find.text('\$5000.00'), findsNothing,
        reason: 'la pantalla ignoró la cola local');
  });

  testWidgets('varias ventas encadenadas agotan el crédito en pantalla', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary', limite: 2000, saldoCache: 0);
      sembrarVentaACreditoPendiente(base, clienteId: 'c1', total: 800, consecutivo: 1);
      sembrarVentaACreditoPendiente(base, clienteId: 'c1', total: 800, consecutivo: 2);
      sembrarVentaACreditoPendiente(base, clienteId: 'c1', total: 800, consecutivo: 3);
    });
    expect(find.byKey(const Key('credito_agotado')), findsOneWidget);
  });

  testWidgets('un cobro sin sincronizar libera línea de inmediato', (tester) async {
    // Si el vendedor acaba de cobrarle en efectivo, la línea se libera ya.
    // Hacerlo esperar a la sincronización sería negarle una venta ya pagada.
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary', limite: 5000, saldoCache: 4800);
      sembrarCobroPendiente(base, clienteId: 'c1', importe: 2000);
    });
    expect(find.text('\$2200.00'), findsOneWidget);
  });

  testWidgets('un cobro cancelado no libera línea', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary', limite: 5000, saldoCache: 4800);
      sembrarCobroPendiente(base, clienteId: 'c1', importe: 2000);
      base.db.execute("UPDATE cobros SET estado='cancelado'");
    });
    expect(find.byKey(const Key('credito_disponible')), findsOneWidget);
    expect(find.text('\$200.00'), findsOneWidget);
  });

  // -------------------------------------------------------------------------
  // La cola a la vista
  // -------------------------------------------------------------------------

  testWidgets('sin pendientes no hay barra que estorbe', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
    });
    expect(find.byKey(const Key('barra_pendientes')), findsNothing);
  });

  testWidgets('con pendientes la barra los muestra', (tester) async {
    // Un número que crece es la primera señal de que algo va mal, y el vendedor
    // es quien lo nota primero.
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
      sembrarPendienteEnCola(base, cuantos: 4);
    });
    expect(find.byKey(const Key('barra_pendientes')), findsOneWidget);
    expect(textoQueContiene('Por enviar: 4'), findsOneWidget);
  });

  testWidgets('un cliente dado de alta en la calle se distingue', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Nueva tiendita', esLocal: true);
    });
    expect(find.byIcon(Icons.cloud_upload_outlined), findsWidgets);
  });
}
