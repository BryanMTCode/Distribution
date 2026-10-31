/// La app del vendedor ya no registra mermas, devoluciones ni cambios (ADR 0002
/// §197).
///
/// «Quiero que en la aplicación no haya nada de devolución, mermas ni nada»:
/// el vendedor lleva lo malo a la bodega, ahí se le cambia por bueno, y la
/// oficina lo registra en el dashboard. De la app se quitaron «Registrar merma»,
/// «Me devolvió mercancía», «Cambio físico» y «Devolver a la bodega». Lo demás
/// de esos menús se queda.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

Future<void> _entrar(WidgetTester tester) async {
  await montarApp(
    tester,
    credencial: credencialDelServidor(),
    sembrar: (b) {
      sembrarEscenarioDeVenta(b);
      sembrarParaCobrar(b);
      sembrarMotivos(b);
    },
  );
  await entrarCon(tester, pinCorrecto);
}

void main() {
  testWidgets('el menú «Más» ya no tiene «Registrar merma»', (tester) async {
    await _entrar(tester);
    await tocar(tester, const Key('menu_vendedor'));
    expect(find.byKey(const Key('boton_merma')), findsNothing);
    expect(find.text('Registrar merma'), findsNothing);
    // Lo demás se queda.
    expect(find.byKey(const Key('boton_impresora')), findsOneWidget);
    expect(find.byKey(const Key('boton_salir')), findsOneWidget);
  });

  testWidgets('la visita ya no tiene devolución ni cambio físico', (tester) async {
    await _entrar(tester);
    await tocar(tester, const Key('cliente_cliente-1'));
    await tocar(tester, const Key('menu_de_visita'));
    expect(find.text('Me devolvió mercancía'), findsNothing);
    expect(find.byKey(const Key('opcion_cambio')), findsNothing);
    expect(find.text('Cambio físico'), findsNothing);
    // «Perfil y ubicación» y «No me compró» se quedan.
    expect(find.byKey(const Key('opcion_perfil_cliente')), findsOneWidget);
    expect(find.text('No me compró'), findsOneWidget);
  });

  testWidgets('«Mi camión» ya no tiene «Devolver a la bodega»', (tester) async {
    await _entrar(tester);
    await tocar(tester, const Key('boton_camion'));
    expect(find.text('Mi camión'), findsWidgets);
    expect(find.byKey(const Key('boton_devolver_a_bodega')), findsNothing);
  });
}
