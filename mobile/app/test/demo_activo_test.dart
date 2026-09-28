/// El modo demo, con el `--dart-define` puesto.
///
/// Estas pruebas solo corren cuando la app se compila con
/// `--dart-define=DSD_DEMO=true`; sin él se omiten, porque el botón que
/// ejercitan no existe en el binario.
///
///     flutter test --dart-define=DSD_DEMO=true
///
/// Verifican lo que `demo_test.dart` no puede: que el atajo, además de estar
/// bien encerrado, de verdad sirva para lo que se hizo.
library;

import 'package:dsd_app/src/datos/servicio_ubicacion.dart';
import 'package:dsd_app/src/demo.dart';
import 'package:dsd_app/src/estado/alta.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

void main() {
  // `testWidgets` solo acepta un booleano en `skip`, a diferencia de `test`.
  // Sin el define, el botón que estas pruebas ejercitan no existe en el
  // binario, así que se omiten en vez de fallar.
  final soloEnModoDemo = !modoDemoDisponible;

  testWidgets('el botón aparece y entra directo a la ruta', (tester) async {
    // Arranca con la base limpia y sin credencial: es exactamente el estado de
    // una instalación nueva, que es donde el login normal exige conexión.
    await montarApp(
      tester,
      extras: [
        servicioUbicacionProvider.overrideWithValue(
          ServicioUbicacionFalso.siempre(
            GpsObtenido(
              Ubicacion(
                lat: 20.6597,
                lng: -103.3496,
                origen: OrigenUbicacion.gps,
                precisionMetros: 9,
              ),
            ),
          ),
        ),
      ],
    );

    expect(find.byKey(const Key('boton_modo_demo')), findsOneWidget);
    expect(textoQueContiene('PIN de demo: $pinDemo'), findsOneWidget);

    await tester.tap(find.byKey(const Key('boton_modo_demo')));
    // El login de demo recorre Argon2id igual que el normal: tarda.
    await tester.pumpAndSettle(const Duration(seconds: 10));

    // Entró, y la ruta viene con datos.
    expect(find.text('Mi ruta'), findsOneWidget);
    expect(find.byKey(const Key('marca_demo')), findsOneWidget);
    expect(find.text('Abarrotes Doña Mary'), findsOneWidget);
  }, skip: soloEnModoDemo);

  testWidgets('la ruta sembrada muestra los cuatro estados de crédito',
      (tester) async {
    await montarApp(
      tester,
      extras: [
        servicioUbicacionProvider
            .overrideWithValue(ServicioUbicacionFalso.siempre(const GpsSinLectura())),
      ],
    );
    await tester.tap(find.byKey(const Key('boton_modo_demo')));
    await tester.pumpAndSettle(const Duration(seconds: 10));

    // Es para lo que sirve la demo: ver de un golpe lo que la lista distingue.
    expect(find.byKey(const Key('credito_disponible')), findsWidgets);
    expect(find.byKey(const Key('credito_agotado')), findsOneWidget);
    expect(find.byKey(const Key('credito_bloqueado')), findsOneWidget);
    expect(find.byKey(const Key('credito_solo_contado')), findsOneWidget);
  }, skip: soloEnModoDemo);

  testWidgets('el disponible ya viene descontado por la venta encolada',
      (tester) async {
    // La Esquina de Ñoño: límite 3000, saldo 900, venta encolada de 1500.
    // Disponible = 3000 - 900 - 1500 = 600.
    await montarApp(
      tester,
      extras: [
        servicioUbicacionProvider
            .overrideWithValue(ServicioUbicacionFalso.siempre(const GpsSinLectura())),
      ],
    );
    await tester.tap(find.byKey(const Key('boton_modo_demo')));
    await tester.pumpAndSettle(const Duration(seconds: 10));

    expect(find.text('\$600.00'), findsOneWidget);
  }, skip: soloEnModoDemo);

  testWidgets('sin señal de GPS la siembra usa coordenadas fijas y no falla',
      (tester) async {
    await montarApp(
      tester,
      extras: [
        servicioUbicacionProvider
            .overrideWithValue(ServicioUbicacionFalso.siempre(const GpsApagado())),
      ],
    );
    await tester.tap(find.byKey(const Key('boton_modo_demo')));
    await tester.pumpAndSettle(const Duration(seconds: 10));

    expect(find.byKey(const Key('marca_demo')), findsOneWidget);
  }, skip: soloEnModoDemo);
}
