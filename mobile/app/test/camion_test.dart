/// «Mi camión»: lo que el vendedor trae ahora mismo.
///
/// La regla de negocio que se prueba aquí: el número es un SALDO. La carga lo
/// sube, la venta lo baja, y la pantalla no recalcula nada. Si recalculara habría
/// dos cuentas del mismo inventario, y el día que discreparan nadie sabría cuál
/// creer.
library;

import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
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

  testWidgets('después de vender, «Mi camión» muestra lo que queda', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) {
        sembrarEscenarioDeVenta(base);
        sembrarParaCobrar(base);
      },
      extras: [
        servicioUbicacionProvider.overrideWithValue(
          ServicioUbicacionFalso.siempre(const GpsSinLectura()),
        ),
      ],
    );
    await entrarCon(tester, pinCorrecto);

    // El vendedor revisa su camión en la mañana: 240 piezas, 10 cajas.
    await abrirCamion(tester);
    expect(textoQueContiene('240.000'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();

    await vender2CajasDeSopa(tester);

    // Y a media ruta lo vuelve a abrir: 2 cajas son 48 piezas, quedan 192.
    await abrirCamion(tester);
    expect(textoQueContiene('192.000'), findsOneWidget,
        reason: 'la venta ya descontó la base local; la pantalla tiene que verlo');
    expect(textoQueContiene('240.000'), findsNothing,
        reason: 'lo de la mañana ya no está en el camión');
  });

}

// ===========================================================================
// El defecto de campo de octubre: «vendo y el camión no baja»
// ===========================================================================
// La venta SÍ descontaba la base local, en la misma transacción que el
// documento. Lo que no bajaba era la PANTALLA: el proveedor que lee «Mi camión»
// se calculaba la primera vez que se abría y nadie lo volvía a calcular, porque
// cada lugar que cambia el camión —la venta, la merma, la devolución, la
// sincronización— invalidaba su propia lista de proveedores, y ninguna incluía
// éste. El vendedor abría su camión a media ruta y veía lo que traía en la
// mañana.
//
// Estas pruebas recorren el camino real —abrir el camión, salir, vender, volver—
// porque una prueba que monta la pantalla después de la venta la calcula fresca
// y pasa aunque el defecto siga ahí.
Future<void> vender2CajasDeSopa(WidgetTester tester) async {
  await tocar(tester, const Key('cliente_cliente-1'));
  await tocar(tester, const Key('agregar_p-sopa|CAJA'));
  await tocar(tester, const Key('mas_p-sopa|CAJA'));
  await tocar(tester, const Key('boton_ver_carrito'));
  await tocar(tester, const Key('boton_cobrar'));
  await tocar(tester, const Key('boton_terminar_visita'));
}

