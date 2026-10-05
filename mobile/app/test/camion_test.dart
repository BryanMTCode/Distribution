/// «Mi camión»: lo que el vendedor trae ahora mismo.
///
/// La regla de negocio que se prueba aquí: el número es un SALDO. La carga lo
/// sube, la venta lo baja, y la pantalla no recalcula nada. Si recalculara habría
/// dos cuentas del mismo inventario, y el día que discreparan nadie sabría cuál
/// creer.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  Future<void> abrirCamion(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('boton_camion')));
    await tester.pumpAndSettle();
  }

  testWidgets('muestra lo que queda, no lo que se cargó', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) => sembrarEnElCamion(
        base,
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo',
        cargada: 240,
        actual: 192,
      ),
    );
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    expect(textoQueContiene('192.000'), findsOneWidget,
        reason: 'lo que queda es lo que el vendedor va a contar en la caja');
    expect(textoQueContiene('240.000'), findsNothing,
        reason: 'lo cargado ya no es lo que trae: confundirlos es prometer '
            'mercancía que no está');
  });

  testWidgets('traduce a cajas sin perder piezas', (tester) async {
    // 192 piezas de una caja de 24 son 8 cajas exactas. Con aritmética de doubles
    // esto se vuelve 7.9999, y el vendedor no sabe si puede surtir 8 cajas.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) => sembrarEnElCamion(
        base,
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo',
        cargada: 240,
        actual: 192,
        porCaja: 24,
      ),
    );
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    expect(textoQueContiene('8 CAJA'), findsOneWidget);
  });

  testWidgets('un negativo se muestra y se destaca', (tester) async {
    // Se vendió más de lo que el sistema creía. No se esconde: la liquidación lo
    // va a cobrar, y el vendedor tiene derecho a verlo antes que la oficina.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) => sembrarEnElCamion(
        base,
        sku: 'ACEITE-900',
        nombre: 'Aceite 900 ml',
        cargada: 48,
        actual: -3,
      ),
    );
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    expect(textoQueContiene('-3.000'), findsOneWidget);
    expect(find.byKey(const Key('resumen_negativos')), findsOneWidget);
  });

  testWidgets('un camión vacío lo dice en lugar de quedarse en blanco',
      (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    expect(textoQueContiene('El camión está vacío'), findsOneWidget);
  });

  testWidgets('se puede buscar un producto', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarEnElCamion(base, sku: 'SOPA-70G', nombre: 'Sopa de fideo',
            cargada: 240, actual: 192);
        sembrarEnElCamion(base, sku: 'FRIJOL-1K', nombre: 'Frijol bayo',
            cargada: 100, actual: 70);
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    await tester.enterText(find.byKey(const Key('buscar_en_camion')), 'Frijol');
    await tester.pumpAndSettle();

    expect(textoQueContiene('Frijol bayo'), findsOneWidget);
    expect(textoQueContiene('Sopa de fideo'), findsNothing);
  });

  testWidgets('AVISA CUANDO LA OFICINA AJUSTÓ EL CAMIÓN, con su nota', (tester) async {
    // Gerencia puede corregir el inventario del camión desde el panel. Si solo
    // cambiara el número, el vendedor vería 12 donde ayer había 30 y no sabría si
    // se lo ajustaron o si la app perdió una carga.
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarEnElCamion(
          base,
          sku: 'SOPA-70G',
          nombre: 'Sopa de fideo',
          cargada: 240,
          actual: 12,
        );
        base.db.execute(
          "INSERT INTO ajustes_camion_aplicados (ajuste_id, folio, nota, aplicado_en) "
          "VALUES ('aj-1', 'AC-000001', 'Juan reportó que trae 12, no 30', "
          "        '2026-10-05T18:00:00Z')",
        );
      },
    );
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    expect(find.byKey(const Key('aviso_ajustes_oficina')), findsOneWidget);
    expect(textoQueContiene('La oficina ajustó tu camión'), findsOneWidget);
    expect(textoQueContiene('Juan reportó que trae 12, no 30'), findsOneWidget);
  });

  testWidgets('sin ajustes no estorba con el aviso', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) => sembrarEnElCamion(
        base,
        sku: 'SOPA-70G',
        nombre: 'Sopa de fideo',
        cargada: 240,
        actual: 192,
      ),
    );
    await entrarCon(tester, pinCorrecto);
    await abrirCamion(tester);

    expect(find.byKey(const Key('aviso_ajustes_oficina')), findsNothing);
  });
}
