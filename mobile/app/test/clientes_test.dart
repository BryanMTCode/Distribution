/// Lista de clientes de la ruta.
///
/// Todo es de contado (ADR 0002 §81): la lista ya no muestra disponible, saldo
/// ni bloqueo; muestra el orden de visita, lo visitado y lo que falta por subir.
library;

import 'package:dsd_app/src/marca.dart';
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

  testWidgets('ABRE EN «HOY» CON LOS QUE TOCAN HOY, Y ABAJO LAS DEMÁS', (tester) async {
    // ADR 0002 §108: el vendedor también pasa a dejarle algo a una tienda que
    // hoy no le toca; las demás de la ruta siguen ahí, abajo.
    await entrar(tester, sembrarRutaConPlan);

    expect(find.text('Hoy · 1'), findsOneWidget);
    final jueves = tester.getTopLeft(find.text('Toca el jueves')).dy;
    final encabezado = tester.getTopLeft(find.byKey(const Key('otras_de_la_ruta'))).dy;
    final lunes = tester.getTopLeft(find.text('Toca el lunes')).dy;
    expect(jueves < encabezado && encabezado < lunes, isTrue);
    expect(textoQueContiene('Las demás de tu ruta · 2'), findsOneWidget);
    expect(find.text('Sin plan'), findsOneWidget);

    // Se pliegan con un toque.
    await tester.tap(find.byKey(const Key('otras_de_la_ruta')));
    await tester.pumpAndSettle();
    expect(find.text('Toca el lunes'), findsNothing);
    expect(find.text('Toca el jueves'), findsOneWidget);

    // «Todos» es la ruta entera, por día (ADR 0002 §123), sin el encabezado de
    // «las demás».
    await tester.tap(find.text('Todos'));
    await tester.pumpAndSettle();
    expect(find.text('Toca el lunes'), findsOneWidget);
    expect(find.text('Sin plan'), findsOneWidget);
    expect(find.byKey(const Key('otras_de_la_ruta')), findsNothing);
  });

  testWidgets('una ruta de más de 200 tiendas sale completa en «Hoy»', (tester) async {
    // Bug de campo, octubre 2026: la lista traía solo las primeras 200 tiendas
    // en orden de ruta, y las rutas tienen 250–270. Las del final, y las que no
    // tienen número de orden, no salían en «Hoy»: el día se veía con menos.
    await entrar(tester, (base) {
      for (var i = 1; i <= 260; i++) {
        // Las últimas diez sin número de orden: van al final de la lista.
        sembrarCliente(base, id: 't$i', nombre: 'Tienda $i', secuencia: i <= 250 ? i : null);
        conPlan(base, 't$i', i.isEven ? '[{"dia":4,"semana":null}]' : '[{"dia":1,"semana":null}]');
      }
    });

    // Con el tope salían 100: las pares de las primeras 200.
    expect(find.text('Hoy · 130'), findsOneWidget);
    expect(textoQueContiene('Visitados 0 de 130'), findsOneWidget);
  });

  testWidgets('las tiendas van agrupadas por día (ADR 0002 §123)', (tester) async {
    await entrar(tester, (base) {
      // El número de la hoja se repite cada día: sin agrupar se mezclaban.
      sembrarCliente(base, id: 'l1', nombre: 'Lunes primera', secuencia: 1);
      sembrarCliente(base, id: 'l2', nombre: 'Lunes segunda', secuencia: 2);
      sembrarCliente(base, id: 'm1', nombre: 'Martes primera', secuencia: 1);
      sembrarCliente(base, id: 'j1', nombre: 'Jueves primera', secuencia: 1);
      sembrarCliente(base, id: 'lj', nombre: 'Lunes y jueves', secuencia: 3);
      sembrarCliente(base, id: 's0', nombre: 'Sin día', secuencia: 9);
      conPlan(base, 'l1', '[{"dia":1,"semana":null}]');
      conPlan(base, 'l2', '[{"dia":1,"semana":null}]');
      conPlan(base, 'm1', '[{"dia":2,"semana":null}]');
      conPlan(base, 'j1', '[{"dia":4,"semana":null}]');
      conPlan(base, 'lj', '[{"dia":1,"semana":null},{"dia":4,"semana":null}]');
    });

    // «Hoy» (jueves): las suyas arriba, y las demás de la ruta por día.
    expect(find.text('Hoy · 2'), findsOneWidget);
    expect(find.byKey(const Key('dia_otras_1')), findsOneWidget);
    expect(find.text('Lunes · 2'), findsOneWidget);
    expect(find.byKey(const Key('dia_otras_2')), findsOneWidget);
    expect(find.byKey(const Key('dia_otras_sin')), findsOneWidget);

    await tester.tap(find.text('Todos'));
    await tester.pumpAndSettle();
    // Cada día con su cuenta, en orden; el de hoy marcado; la que toca dos días,
    // en los dos.
    double y(String texto) => tester.getTopLeft(find.text(texto).first).dy;
    expect(find.text('Lunes · 3'), findsOneWidget);
    expect(find.text('Jueves · 2'), findsOneWidget);
    expect(find.text('hoy'), findsOneWidget);
    expect(y('Lunes · 3') < y('Lunes primera'), isTrue);
    expect(y('Lunes primera') < y('Lunes segunda'), isTrue);
    expect(y('Lunes segunda') < y('Martes · 1'), isTrue);
    expect(y('Martes · 1') < y('Martes primera'), isTrue);
    expect(find.text('Lunes y jueves'), findsNWidgets(2));
    expect(find.text('Sin día de visita · 1'), findsOneWidget);

    // Un día se pliega con un toque, y los demás se quedan.
    await tester.tap(find.byKey(const Key('dia_ruta_1')));
    await tester.pumpAndSettle();
    expect(find.text('Lunes primera'), findsNothing);
    expect(find.text('Lunes y jueves'), findsOneWidget);
    expect(find.text('Martes primera'), findsOneWidget);
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
    // Estuvo, pero no le vendió: gris con una equis, no el verde de la venta
    // (ADR 0002 §153).
    expect(find.byKey(const Key('sin_venta_c1')), findsOneWidget);
    expect(
      tester.widget<CircleAvatar>(find.byKey(const Key('visitado_c1'))).backgroundColor,
      colorDeSinVenta,
    );
  });

  testWidgets('la que ya compró hoy va en verde; las que faltan, rojas con su número',
      (tester) async {
    // «Los que ya les vendí cámbiales de color: que las otras sigan apareciendo
    // rojo con el número, pero los demás que tengan otro color.»
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Ya compró', secuencia: 1);
      sembrarCliente(base, id: 'c2', nombre: 'Falta', secuencia: 2);
      sembrarCliente(base, id: 'c3', nombre: 'Le cancelaron', secuencia: 3);
      // La que solo tiene una venta cancelada: como si no se le hubiera vendido.
      base.db.execute(
        "INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo, subtotal, "
        "total, fecha_dispositivo, fecha_operativa, creado_en) "
        "VALUES ('v1', 1, 'VEND01-000001', 'c1', 'contado', 100, 100, "
        "'2026-09-24T07:00:00.000Z', '2026-09-24', '2026-09-24T07:00:00.000Z')",
      );
      // «Si se cancela una venta de cierto vendedor y cierta tienda, que vuelva a
      // aparecer en la lista con el número, como si aún no se le vendiera nada»
      // (ADR 0002 §156).
      base.db.execute(
        "INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo, subtotal, "
        "total, fecha_dispositivo, fecha_operativa, estado, creado_en) "
        "VALUES ('v2', 2, 'VEND01-000002', 'c3', 'contado', 50, 50, "
        "'2026-09-24T07:10:00.000Z', '2026-09-24', 'cancelada', '2026-09-24T07:10:00.000Z')",
      );
    });
    expect(find.byKey(const Key('vendida_c1')), findsOneWidget);
    expect(
      tester.widget<CircleAvatar>(find.byKey(const Key('visitado_c1'))).backgroundColor,
      colorDeVendida,
    );
    // La que falta sigue con su número, en el color de la marca.
    expect(find.byKey(const Key('visitado_c2')), findsNothing);
    expect(find.text('2'), findsOneWidget);
    expect(find.byKey(const Key('visitado_c3')), findsNothing);
    expect(find.text('3'), findsOneWidget);
  });

  testWidgets('si la hora del teléfono está mal, se avisa (§157)', (tester) async {
    // Con esa hora se decide de qué día es cada venta.
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Primera', secuencia: 1);
      base.db.execute(
        "INSERT INTO sync_estado (clave, valor) VALUES ('desfase_reloj_seg', '3600')",
      );
    });
    expect(find.byKey(const Key('aviso_reloj_desfasado')), findsOneWidget);
    expect(textoQueContiene('La hora del teléfono va 1 h atrasada'), findsOneWidget);
  });

  testWidgets('con la hora bien, o unos segundos de red, no se avisa', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Primera', secuencia: 1);
      base.db.execute(
        "INSERT INTO sync_estado (clave, valor) VALUES ('desfase_reloj_seg', '-40')",
      );
    });
    expect(find.byKey(const Key('aviso_reloj_desfasado')), findsNothing);
  });

  testWidgets('sin plan en ningún cliente, la lista es la de siempre', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Primera', secuencia: 1);
    });
    expect(find.byKey(const Key('filtro_hoy')), findsNothing);
    expect(find.text('Primera'), findsOneWidget);
  });

  testWidgets('si hoy no toca nadie, lo dice y abajo siguen las demás', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c2', nombre: 'Toca el lunes', secuencia: 1);
      conPlan(base, 'c2', '[{"dia":1,"semana":null}]');
    });
    expect(find.textContaining('Hoy no te toca nadie'), findsOneWidget);
    expect(find.text('Toca el lunes'), findsOneWidget);
  });

  // -------------------------------------------------------------------------
  // La visita reprogramada (ADR 0002 §103)
  // -------------------------------------------------------------------------
  testWidgets('la tienda reprogramada para hoy sale en «Hoy» y dice por qué',
      (tester) async {
    await entrar(tester, (base) {
      sembrarRutaConPlan(base);
      // «Toca el lunes» pidió que volvieran hoy, jueves 24.
      base.db.execute(
        "UPDATE clientes SET visita_reprogramada = '2026-09-24' WHERE id = 'c2'",
      );
    });
    expect(find.text('Hoy · 2'), findsOneWidget);
    expect(find.text('Toca el lunes'), findsOneWidget);
    expect(find.byKey(const Key('reprogramada_c2')), findsOneWidget);
    expect(textoQueContiene('Te pidió que volvieras hoy'), findsOneWidget);
  });

  testWidgets('la que se reprogramó en este teléfono dice cuándo volver', (tester) async {
    await entrar(tester, (base) {
      sembrarRutaConPlan(base);
      sembrarMotivos(base);
      // El no-drop de hoy en el jueves, todavía sin subir: «ven el lunes 28».
      base.db.execute(
        "INSERT INTO no_drops (id, folio_consecutivo, cliente_id, motivo_codigo, lat, lng, "
        "fecha_dispositivo, fecha_operativa, reprogramada_para) "
        "VALUES ('n1', 1, 'c1', 'CERRADO', 20.6, -103.3, '2026-09-24T07:00:00.000Z', "
        "'2026-09-24', '2026-09-28')",
      );
    });
    expect(find.byKey(const Key('visitado_c1')), findsOneWidget);
    expect(find.byKey(const Key('reprogramada_c1')), findsOneWidget);
    expect(textoQueContiene('Volver el lunes 28 de septiembre'), findsOneWidget);
  });

  testWidgets('la ubicación se abre desde la lista, sin entrar a la visita', (tester) async {
    await entrar(tester, (base) {
      sembrarCliente(base, id: 'c1', nombre: 'Sin ubicación todavía');
    });
    await tester.tap(find.byKey(const Key('ubicar_c1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pantalla_perfil_cliente')), findsOneWidget);
    expect(textoQueContiene('Ubicación del negocio'), findsOneWidget);
  });
}
