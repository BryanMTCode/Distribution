/// «Mi día» usa el MISMO día que estampa la venta.
///
/// Bug de campo, octubre 2026: la pantalla calculaba el día en UTC y los
/// documentos en hora local. De las 18:00 en adelante (centro de México) el día
/// UTC ya es mañana: la venta llegaba al servidor y «Mi día» no la mostraba.
library;

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  test('el día de «Mi día» es el de los documentos, también de noche', () {
    // 01:30 UTC del 7 de octubre: en México todavía son las 19:30 del 6.
    final instante = DateTime.utc(2026, 10, 7, 1, 30);
    final contenedor = ProviderContainer(
      overrides: [relojProvider.overrideWithValue(() => instante)],
    );
    addTearDown(contenedor.dispose);

    expect(contenedor.read(diaOperativoProvider), equals(diaOperativoDe(instante)));
  });

  test('el día cambia solo al volver a mirar el reloj (ADR 0002 §164)', () {
    // Bug de campo: la app se abrió el martes y seguía viva el miércoles; «Mi
    // día» decía martes 20 y no dejaba pasar a hoy.
    var ahora = DateTime(2026, 10, 20, 23, 50);
    final contenedor = ProviderContainer(
      overrides: [relojProvider.overrideWithValue(() => ahora)],
    );
    addTearDown(contenedor.dispose);

    expect(contenedor.read(diaOperativoProvider), '2026-10-20');
    ahora = DateTime(2026, 10, 21, 8);
    contenedor.read(diaOperativoProvider.notifier).revisar();
    expect(contenedor.read(diaOperativoProvider), '2026-10-21');
  });

  testWidgets('al volver a la app, «Mi día» y «Hoy» ya son del día nuevo', (tester) async {
    // Martes 20 en la noche; la app se queda abierta y vuelve el miércoles 21.
    var ahora = DateTime(2026, 10, 20, 21);
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      reloj: () => ahora,
      sembrar: (base) {
        sembrarCliente(base, id: 'c-ma', nombre: 'Toca el martes', secuencia: 1);
        sembrarCliente(base, id: 'c-mi', nombre: 'Toca el miércoles', secuencia: 1);
        base.db.execute(
          "UPDATE clientes SET plan_visita = '[{\"dia\":2,\"semana\":null}]' WHERE id = 'c-ma'",
        );
        base.db.execute(
          "UPDATE clientes SET plan_visita = '[{\"dia\":3,\"semana\":null}]' WHERE id = 'c-mi'",
        );
      },
    );
    await entrarCon(tester, pinCorrecto);
    expect(find.text('Hoy · 1'), findsOneWidget);
    final martes = tester.getTopLeft(find.text('Toca el martes')).dy;
    expect(martes < tester.getTopLeft(find.byKey(const Key('otras_de_la_ruta'))).dy, isTrue);

    // Pasa la noche con la app en segundo plano y el vendedor vuelve.
    ahora = DateTime(2026, 10, 21, 8);
    for (final estado in [
      AppLifecycleState.inactive,
      AppLifecycleState.hidden,
      AppLifecycleState.paused,
      AppLifecycleState.hidden,
      AppLifecycleState.inactive,
      AppLifecycleState.resumed,
    ]) {
      tester.binding.handleAppLifecycleStateChanged(estado);
    }
    await tester.pumpAndSettle();

    // «Hoy» ya es la del miércoles.
    final miercoles = tester.getTopLeft(find.text('Toca el miércoles')).dy;
    expect(miercoles < tester.getTopLeft(find.byKey(const Key('otras_de_la_ruta'))).dy, isTrue);

    // Y «Mi día» abre en el miércoles 21.
    await tester.tap(find.byKey(const Key('boton_mi_dia')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('21 de octubre'), findsWidgets);
    expect(textoQueContiene('20 de octubre'), findsNothing);
  });
}
