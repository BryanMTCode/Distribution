/// Lista de clientes de la ruta.
///
/// Todo es de contado (ADR 0002 §81): la lista ya no muestra disponible, saldo
/// ni bloqueo; muestra el orden de visita, lo visitado y lo que falta por subir.
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
  // Todo es de contado
  // -------------------------------------------------------------------------

  testWidgets('la lista ya no habla de crédito, saldo ni bloqueo', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
      // Las columnas del crédito siguen en la base local de un teléfono que
      // viene de la versión anterior: nada las lee.
      base.db.execute(
        'UPDATE clientes SET permite_credito = 1, limite_credito = 5000, '
        'saldo_cache = 4800, bloqueado = 1',
      );
    });
    expect(find.text('Doña Mary'), findsOneWidget);
    expect(find.textContaining('crédito'), findsNothing);
    expect(find.textContaining('isponible'), findsNothing);
    expect(find.textContaining('\$'), findsNothing);
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

  // -------------------------------------------------------------------------
  // El plan de visita (migración 0041 del servidor)
  // -------------------------------------------------------------------------
  // El reloj de prueba es el jueves 24 de septiembre de 2026: jueves es 4.
  void conPlan(dynamic base, String id, String plan) => base.db.execute(
        'UPDATE clientes SET plan_visita = ? WHERE id = ?',
        [plan, id],
      );

  void sembrarRutaConPlan(dynamic base) {
    sembrarCliente(base, id: 'c1', nombre: 'Toca el jueves', secuencia: 1);
    sembrarCliente(base, id: 'c2', nombre: 'Toca el lunes', secuencia: 2);
    sembrarCliente(base, id: 'c3', nombre: 'Sin plan', secuencia: 3);
    conPlan(base, 'c1', '[{"dia":4,"semana":null}]');
    conPlan(base, 'c2', '[{"dia":1,"semana":null}]');
  }

  testWidgets('ABRE EN «HOY» CON LOS QUE TOCAN HOY', (tester) async {
    await entrar(tester, sembrarRutaConPlan);

    expect(find.text('Hoy · 1'), findsOneWidget);
    expect(find.text('Toca el jueves'), findsOneWidget);
    expect(find.text('Toca el lunes'), findsNothing);
    expect(find.text('Sin plan'), findsNothing);

    await tester.tap(find.text('Todos'));
    await tester.pumpAndSettle();
    expect(find.text('Toca el lunes'), findsOneWidget);
    expect(find.text('Sin plan'), findsOneWidget);
  });

  testWidgets('al buscar se busca en todos, toque o no hoy', (tester) async {
    // El cliente que llama pidiendo mercancía no tiene por qué tocar hoy.
    await entrar(tester, sembrarRutaConPlan);
    await tester.enterText(find.byKey(const Key('campo_busqueda')), 'lunes');
    await tester.pumpAndSettle();
    expect(find.text('Toca el lunes'), findsOneWidget);
  });

  testWidgets('el visitado lleva palomita y cuenta en el avance', (tester) async {
    await entrar(tester, (base) {
      sembrarRutaConPlan(base);
      // Una devolución de hoy: estuvo ahí, aunque no vendiera.
      base.db.execute(
        "INSERT INTO mermas (id, folio_consecutivo, tipo, cliente_id, motivo_codigo, "
        "fecha_dispositivo, fecha_operativa) "
        "VALUES ('m1', 1, 'devolucion_cliente', 'c1', 'CADUCADO', "
        "'2026-09-24T07:00:00.000Z', '2026-09-24')",
      );
    });
    expect(find.byKey(const Key('visitado_c1')), findsOneWidget);
    expect(find.text('Visitados 1 de 1'), findsOneWidget);
  });

  testWidgets('sin plan en ningún cliente, la lista es la de siempre', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Primera', secuencia: 1);
    });
    expect(find.byKey(const Key('filtro_hoy')), findsNothing);
    expect(find.text('Primera'), findsOneWidget);
  });

  testWidgets('si hoy no toca nadie, lo dice y manda a «Todos»', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c2', nombre: 'Toca el lunes', secuencia: 1);
      conPlan(base, 'c2', '[{"dia":1,"semana":null}]');
    });
    expect(find.textContaining('Hoy no te toca nadie'), findsOneWidget);
  });
}
