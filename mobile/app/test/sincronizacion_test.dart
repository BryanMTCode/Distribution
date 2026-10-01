/// La sincronización desde la pantalla.
///
/// Lo que importa aquí es que cada final le diga al vendedor algo distinto:
/// "sin señal" es esperar, "vuelve a entrar" es actuar, y "quedaron con error"
/// es avisar a la oficina.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Transporte programable, igual que el de dsd_core pero para la app.
class TransporteDePrueba implements Transporte {
  TransporteDePrueba({this.codigoPush = 200, this.seCaeLaRed = false});

  final int codigoPush;
  final bool seCaeLaRed;
  final List<String> llamadas = [];

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    llamadas.add(ruta);
    if (seCaeLaRed) throw const ErrorDeRed('sin señal');
    final sobres = (cuerpo['sobres'] as List).cast<Map<String, Object?>>();
    if (codigoPush != 200) return RespuestaHttp(codigoPush, '{"detail":"no"}');
    return RespuestaHttp(
      200,
      jsonEncode({
        'lote_id': cuerpo['lote_id'],
        'aceptadas': sobres.length,
        'duplicadas': 0,
        'rechazadas': 0,
        'resultados': sobres
            .map((s) => {'operacion_id': s['operacion_id'], 'estado': 'aceptada'})
            .toList(),
      }),
    );
  }

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    llamadas.add(ruta);
    if (seCaeLaRed) throw const ErrorDeRed('sin señal');

    // Las órdenes del servidor (Fase 9) se piden al empezar cada corrida. Se
    // contestan aparte y no con la forma del pull: antes de distinguirlas, este
    // transporte devolvía un delta a esa consulta y el parseo lanzaba un
    // `TypeError` — que destapó un fallo de verdad en el sincronizador, y
    // además hacía que estas pruebas probaran un camino que no existe.
    if (ruta.startsWith('/v1/dispositivos/mio')) {
      return const RespuestaHttp(
        200,
        '{"estado":"activo","borrar":false,"borrado_motivo":null,'
        '"dias_max_offline":7,"dias_sin_sincronizar":0}',
      );
    }
    return const RespuestaHttp(200, '{"cursor":7,"hay_mas":false,"cambios":[]}');
  }
}

void main() {
  Future<void> entrarYSincronizar(
    WidgetTester tester, {
    required Transporte? transporte,
    void Function(dynamic base)? sembrar,
  }) async {
    await montarApp(
      tester,
      credencial: credencialDelServidor(),
      sembrar: sembrar,
      extras: [
        transporteProvider.overrideWithValue(transporte),
        tokenProvider.overrideWith((_) => transporte == null ? null : 'token-de-prueba'),
      ],
    );
    await entrarCon(tester, pinCorrecto);
    await tester.tap(find.byKey(const Key('boton_sincronizar')));
    await tester.pumpAndSettle();
  }

  testWidgets('una sincronización sin nada pendiente lo dice', (tester) async {
    await entrarYSincronizar(tester, transporte: TransporteDePrueba());
    expect(find.text('Todo al día.'), findsOneWidget);
  });

  testWidgets('se envía la cola y la barra de pendientes desaparece',
      (tester) async {
    await entrarYSincronizar(
      tester,
      transporte: TransporteDePrueba(),
      sembrar: (base) {
        sembrarCliente(base, id: 'c1', nombre: 'Doña Mary');
        sembrarPendienteEnCola(base, cuantos: 2);
      },
    );

    expect(textoQueContiene('enviadas 2'), findsOneWidget);
    expect(find.byKey(const Key('barra_pendientes')), findsNothing);
  });

  testWidgets('sin señal avisa que nada se perdió', (tester) async {
    // Es el mensaje que evita que el vendedor entre en pánico y vuelva a
    // capturar la venta a mano.
    await entrarYSincronizar(
      tester,
      transporte: TransporteDePrueba(seCaeLaRed: true),
      sembrar: (base) => sembrarPendienteEnCola(base, cuantos: 1),
    );

    expect(textoQueContiene('Nada se perdió'), findsOneWidget);
    expect(find.byKey(const Key('barra_pendientes')), findsOneWidget,
        reason: 'la cola debe seguir a la vista');
  });

  testWidgets('una sesión vencida pide volver a entrar', (tester) async {
    await entrarYSincronizar(
      tester,
      transporte: TransporteDePrueba(codigoPush: 401),
      sembrar: (base) => sembrarPendienteEnCola(base, cuantos: 1),
    );
    expect(textoQueContiene('Vuelve a entrar'), findsOneWidget);
  });

  testWidgets('el servidor caído no pierde la cola', (tester) async {
    await entrarYSincronizar(
      tester,
      transporte: TransporteDePrueba(codigoPush: 503),
      sembrar: (base) => sembrarPendienteEnCola(base, cuantos: 3),
    );
    expect(textoQueContiene('no responde'), findsOneWidget);
    expect(textoQueContiene('Por enviar: 3'), findsOneWidget);
  });

  testWidgets('sin token la app no intenta y lo explica', (tester) async {
    // El vendedor entró offline: es el caso normal a media ruta, no un error.
    await entrarYSincronizar(tester, transporte: null);
    expect(textoQueContiene('Entraste sin señal'), findsOneWidget);
  });
}
