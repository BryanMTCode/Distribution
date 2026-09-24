/// Login sin señal, desde la pantalla.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  testWidgets('con el PIN correcto entra a la ruta', (tester) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: (base) => sembrarCliente(base, id: 'c1', nombre: 'Abarrotes Doña Mary'),
    );

    expect(find.byKey(const Key('campo_pin')), findsOneWidget);
    await entrarCon(tester, pinCorrecto);

    expect(find.text('Mi ruta'), findsOneWidget);
    expect(find.text('Abarrotes Doña Mary'), findsOneWidget);
  });

  testWidgets('el PIN incorrecto no entra y lo dice claro', (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinIncorrecto);

    expect(find.byKey(const Key('aviso_login')), findsOneWidget);
    expect(find.text('PIN incorrecto'), findsOneWidget);
    expect(find.text('Mi ruta'), findsNothing);
  });

  testWidgets('una credencial vencida pide conectarse, no reintentar el PIN',
      (tester) async {
    // El mensaje importa: "PIN incorrecto" y "conéctate" piden cosas distintas
    // del vendedor, y confundirlos lo deja parado a media ruta.
    await montarApp(
      tester,
      credencial: credencialDelServidor(validaHasta: '2026-09-20T00:00:00.000Z'),
      ahora: DateTime.utc(2026, 9, 24, 7),
    );
    await entrarCon(tester, pinCorrecto);

    expect(find.text('Tu acceso venció'), findsOneWidget);
    expect(textoQueContiene('Conéctate a internet'), findsOneWidget);
  });

  testWidgets('sin credencial guardada avisa que hace falta la primera conexión',
      (tester) async {
    await montarApp(tester);
    await entrarCon(tester, pinCorrecto);

    expect(find.text('Este equipo no tiene sesión'), findsOneWidget);
  });

  testWidgets('el perfil de gerencia no cae en la pantalla del vendedor',
      (tester) async {
    // Si cayera, tendría a la vista operaciones que no le corresponden.
    await montarApp(tester, credencial: credencialDelServidor(rol: 'gerente'));
    await entrarCon(tester, pinCorrecto);

    expect(find.byKey(const Key('panel_gerencia')), findsOneWidget);
    expect(find.text('Mi ruta'), findsNothing);
  });

  testWidgets('salir devuelve al login', (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinCorrecto);
    expect(find.text('Mi ruta'), findsOneWidget);

    await tester.tap(find.byKey(const Key('boton_salir')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('campo_pin')), findsOneWidget);
  });
}
