/// El APK compilado sin servidor, y los dos cerrojos que lo delatan.
///
/// `make apk` exige `DSD_BASE_URL` y se niega sin ella, así que el camino normal
/// no puede producir ese APK. Esto cubre el otro camino —`flutter build apk
/// --release` a secas—, que compila sin una queja y deja un binario que se
/// instala, abre, y falla al entrar con un error de red. El vendedor reporta «no
/// hay internet» y nadie mira el binario.
library;

import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_app/src/pantallas/sin_servidor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('sin el define, la dirección queda en el marcador', () {
    // La prueba corre sin `--dart-define=DSD_BASE_URL`, igual que un
    // `flutter build apk --release` a secas. Esto fija que `apkSinServidor`
    // compare contra EL valor que de verdad queda por omisión: si alguien
    // cambia uno de los dos y no el otro, la guarda deja de disparar y el APK
    // malo vuelve a pasar en silencio.
    expect(baseUrlPorOmision, equals(marcadorSinServidor));
  });

  test('en depuración la guarda está apagada', () {
    // `kReleaseMode` es el segundo cerrojo, y va en este orden a propósito:
    // `flutter run` y `flutter test` sin define apuntan al marcador y deben
    // seguir funcionando —el modo demo vive ahí—. La pantalla es solo para un
    // binario de producción.
    expect(apkSinServidor, isFalse);
  });

  testWidgets('la pantalla dice que el problema es el APK, no la señal',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: PantallaSinServidor()),
    );

    expect(find.byKey(const Key('apk_sin_servidor')), findsOneWidget);
    expect(find.textContaining('sin servidor'), findsWidgets);

    // Lo que esta pantalla tiene que lograr: que nadie salga a revisar el túnel
    // ni el router. Si el texto deja de descartar la señal, la pantalla ya no
    // sirve para lo que se hizo.
    expect(find.textContaining('No es falla del teléfono ni de la señal'),
        findsOneWidget);

    // Y el comando que lo arregla, para quien recibe la llamada.
    expect(find.textContaining('make apk DSD_BASE_URL='), findsOneWidget);
  });

  testWidgets('no ofrece ningún botón', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: PantallaSinServidor()),
    );

    // Desde el teléfono no hay nada que hacer: el servidor se fija al compilar
    // y no en una pantalla de ajustes, que es deliberado —un campo editable es
    // el camino para que un equipo robado mande la cartera a otra parte—. Un
    // botón que no sirve haría concluir que la app está rota.
    expect(find.byType(ElevatedButton), findsNothing);
    expect(find.byType(TextButton), findsNothing);
    expect(find.byType(FilledButton), findsNothing);
    expect(find.byType(OutlinedButton), findsNothing);
  });

  testWidgets('avisa de no desinstalar, que es lo que se lleva las ventas',
      (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: PantallaSinServidor()),
    );

    // El caso real y peor: este APK instalado ENCIMA de uno que funcionaba. La
    // base local sigue ahí con lo que no se haya subido, y desinstalar —que es
    // lo primero que se le ocurre a cualquiera con una app que no abre— se lo
    // lleva.
    expect(find.textContaining('NO DESINSTALES'), findsOneWidget);
  });
}
