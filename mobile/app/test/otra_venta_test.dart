/// Otra venta al mismo cliente, en la misma visita (ADR 0002 §160).
///
/// «Si le quieren vender algo nuevo a un cliente, que le hagan otra venta en
/// ese mismo rato.» Lo que defienden estas pruebas:
/// · «Otra venta» vuelve al catálogo del MISMO cliente, con el carrito vacío.
/// · La segunda es otra remisión, con su folio; la primera no se toca.
/// · El camión ya viene descontado de la primera.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Entra y le cobra dos cajas de sopa al cliente 1.
Future<BaseLocal> cobrarDosCajas(
  WidgetTester tester, {
  List<Override> extras = const [],
}) async {
  final base = await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b);
      sembrarParaCobrar(b);
    },
    extras: [
      servicioUbicacionProvider.overrideWithValue(
        ServicioUbicacionFalso.siempre(const GpsSinLectura()),
      ),
      ...extras,
    ],
  );
  await entrarCon(tester, pinCorrecto);
  await tocar(tester, const Key('cliente_cliente-1'));
  await tocar(tester, const Key('agregar_p-sopa|CAJA'));
  await tocar(tester, const Key('mas_p-sopa|CAJA'));
  await tocar(tester, const Key('boton_ver_carrito'));
  await tocar(tester, const Key('boton_cobrar'));
  return base;
}

void main() {
  testWidgets('«Otra venta» vuelve al catálogo del mismo cliente y cobra otra remisión',
      (tester) async {
    final base = await cobrarDosCajas(tester);
    expect(find.byKey(const Key('boton_otra_venta')), findsOneWidget);

    await tocar(tester, const Key('boton_otra_venta'));

    // El catálogo, con el carrito vacío: lo de la primera ya es un documento.
    expect(find.byKey(const Key('agregar_p-sopa|CAJA')), findsOneWidget);
    expect(find.byKey(const Key('folio_de_la_venta')), findsNothing);

    await tocar(tester, const Key('agregar_p-sopa|CAJA'));
    await tocar(tester, const Key('boton_ver_carrito'));
    await tocar(tester, const Key('boton_cobrar'));

    expect(find.text('VEND01-000002'), findsOneWidget);
    final ventas = base.db.select(
      'SELECT folio_local, cliente_id, total FROM ventas ORDER BY folio_consecutivo',
    );
    expect(ventas, hasLength(2));
    expect(ventas.map((v) => v['cliente_id']).toSet(), {'cliente-1'});
    expect(ventas[0]['total'], 592, reason: 'la primera no se toca');
    expect(ventas[1]['total'], 296);
  });

  testWidgets('el camión ya viene descontado de la primera', (tester) async {
    final base = await cobrarDosCajas(tester);
    final db = BaseLocalDePrueba(base);

    await tocar(tester, const Key('boton_otra_venta'));
    await tocar(tester, const Key('agregar_p-sopa|CAJA'));
    await tocar(tester, const Key('boton_ver_carrito'));
    await tocar(tester, const Key('boton_cobrar'));

    // 240 piezas en la mañana, 2 cajas (48) en la primera y 1 (24) en la otra.
    expect(
      db.unaFila(
        "SELECT cant_actual FROM existencias_camion WHERE producto_id = 'p-sopa'",
      )['cant_actual'],
      168,
    );
  });

  testWidgets('«Terminar visita» sigue llevando a la ruta', (tester) async {
    await cobrarDosCajas(tester);
    await tocar(tester, const Key('boton_terminar_visita'));
    expect(find.byKey(const Key('cliente_cliente-1')), findsOneWidget);
    expect(find.byKey(const Key('vendida_cliente-1')), findsOneWidget);
  });
}
