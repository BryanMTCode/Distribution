/// La impresora Bluetooth de verdad (ADR 0002 §139).
///
/// El socket vive en Android (`ImpresoraBluetooth.kt`) y no se puede abrir en
/// una prueba. Lo que sí se prueba es todo lo de este lado del canal:
///
/// 1. **Cada respuesta del teléfono se traduce** a lo que el vendedor tiene que
///    hacer: apagada, Bluetooth apagado, sin permiso, sin canal.
/// 2. **La pantalla**: buscar las emparejadas (las impresoras primero), elegir
///    una —queda guardada en el teléfono—, imprimir la prueba con el ancho del
///    papel y los acentos, y olvidarla.
/// 3. **Los tickets usan la elegida**; sin elegir, ninguna —ya no la simulada,
///    que decía «salió» sin papel—.
library;

import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/datos/impresora.dart';
import 'package:dsd_app/src/datos/impresora_bluetooth.dart';
import 'package:dsd_app/src/estado/carrito.dart';
import 'package:dsd_app/src/estado/impresora.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

const _mp200 = ImpresoraEmparejada(
  nombre: 'EC-MP200',
  direccion: '66:22:A1:3F:00:12',
  pareceImpresora: true,
);

/// El teléfono de mentira: contesta el canal como lo haría Android.
class _Telefono {
  _Telefono({this.codigoAlImprimir = 'ok', this.emparejadas});

  String codigoAlImprimir;
  final Map<String, Object?>? emparejadas;
  final List<MethodCall> llamadas = [];

  Future<Object?> contestar(MethodCall llamada) async {
    llamadas.add(llamada);
    if (llamada.method == 'emparejadas') {
      return emparejadas ??
          {
            'codigo': 'ok',
            'impresoras': [
              {'nombre': 'Audífonos JBL', 'direccion': '11:11:11:11:11:11', 'es_impresora': false},
              {'nombre': 'EC-MP200', 'direccion': '66:22:A1:3F:00:12', 'es_impresora': true},
            ],
          };
    }
    if (llamada.method == 'imprimir') {
      return codigoAlImprimir == 'ok'
          ? {'codigo': 'ok', 'bytes': (llamada.arguments as Map)['bytes'].length}
          : {'codigo': codigoAlImprimir, 'detalle': 'lo que diga Android'};
    }
    return null;
  }
}

void _conectar(_Telefono? telefono) {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(canalImpresora, telefono?.contestar);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  tearDown(() => _conectar(null));

  group('lo que contesta el teléfono', () {
    test('imprimir manda la dirección y los bytes, y «ok» es impresa', () async {
      final telefono = _Telefono();
      _conectar(telefono);
      final r = await const ImpresoraBluetooth(elegida: _mp200).imprimir([0x1B, 0x40, 0x41]);
      expect(r, isA<ImpresionHecha>());
      expect((r as ImpresionHecha).bytesEnviados, 3);
      final args = telefono.llamadas.single.arguments as Map;
      expect(args['direccion'], '66:22:A1:3F:00:12');
      expect(args['bytes'], isA<Uint8List>());
      expect(args['bytes'], [0x1B, 0x40, 0x41]);
    });

    test('apagada o lejos es «desconectada»', () async {
      _conectar(_Telefono(codigoAlImprimir: 'no_conecta'));
      expect(await const ImpresoraBluetooth(elegida: _mp200).imprimir([1]),
          isA<ImpresoraDesconectada>());
    });

    test('Bluetooth apagado y sin permiso dicen qué hacer', () async {
      _conectar(_Telefono(codigoAlImprimir: 'bluetooth_apagado'));
      final apagado = await const ImpresoraBluetooth(elegida: _mp200).imprimir([1]);
      expect((apagado as ImpresionFallida).detalle, contains('préndelo'));

      _conectar(_Telefono(codigoAlImprimir: 'sin_permiso'));
      final permiso = await const ImpresoraBluetooth(elegida: _mp200).imprimir([1]);
      expect((permiso as ImpresionFallida).detalle, contains('Dispositivos cercanos'));
    });

    test('sin el canal (no es Android) no truena', () async {
      _conectar(null);
      final r = await const ImpresoraBluetooth(elegida: _mp200).imprimir([1]);
      expect(r, isA<ImpresionFallida>());
      final e = await buscarEmparejadas();
      expect(e.problema, contains('no puede usar una impresora Bluetooth'));
    });

    test('las emparejadas: las impresoras primero', () async {
      _conectar(_Telefono());
      final e = await buscarEmparejadas();
      expect(e.problema, isNull);
      expect(e.impresoras.map((i) => i.nombre), ['EC-MP200', 'Audífonos JBL']);
    });

    test('buscar con el Bluetooth apagado lo dice', () async {
      _conectar(_Telefono(emparejadas: {'codigo': 'bluetooth_apagado'}));
      expect((await buscarEmparejadas()).problema, contains('Bluetooth del teléfono está apagado'));
    });
  });

  group('qué impresora usan los tickets', () {
    ProviderContainer contenedor() {
      final base = BaseLocal.enMemoria();
      addTearDown(base.cerrar);
      final c = ProviderContainer(overrides: [baseLocalProvider.overrideWithValue(base)]);
      addTearDown(c.dispose);
      return c;
    }

    test('sin elegir, ninguna: ya no la simulada', () {
      expect(contenedor().read(impresoraProvider), isA<SinImpresora>());
    });

    test('la elegida, por Bluetooth, y se queda guardada en el teléfono', () {
      final c = contenedor();
      c.read(impresoraElegidaProvider.notifier).elegir(_mp200);
      final impresora = c.read(impresoraProvider);
      expect(impresora, isA<ImpresoraBluetooth>());
      expect(impresora.nombre, 'EC-MP200');
      // Guardada: un contenedor nuevo sobre la misma base la recuerda.
      final otro = ProviderContainer(overrides: [
        baseLocalProvider.overrideWithValue(c.read(baseLocalProvider)),
      ]);
      addTearDown(otro.dispose);
      expect(otro.read(impresoraElegidaProvider)?.direccion, '66:22:A1:3F:00:12');

      c.read(impresoraElegidaProvider.notifier).olvidar();
      expect(c.read(impresoraProvider), isA<SinImpresora>());
    });
  });

  group('la pantalla', () {
    testWidgets('buscar, elegir e imprimir la prueba, desde el menú de «Mi ruta»',
        (tester) async {
      final telefono = _Telefono();
      _conectar(telefono);
      final base = await montarApp(tester, credencial: credencialDelServidor());
      await entrarCon(tester, pinCorrecto);
      await tocarEnElMenu(tester, const Key('boton_impresora'));
      expect(find.byKey(const Key('pantalla_impresora')), findsOneWidget);
      expect(find.text('Ninguna elegida'), findsOneWidget);
      expect(
        tester.widget<FilledButton>(find.byKey(const Key('boton_imprimir_prueba'))).onPressed,
        isNull,
      );

      await tester.tap(find.byKey(const Key('boton_buscar_impresoras')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('emparejada_66:22:A1:3F:00:12')), findsOneWidget);
      expect(find.byKey(const Key('emparejada_11:11:11:11:11:11')), findsOneWidget);

      await tester.tap(find.byKey(const Key('emparejada_66:22:A1:3F:00:12')));
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.byKey(const Key('impresora_elegida'))).data, 'EC-MP200');
      final guardada = base.db.select(
          "SELECT valor FROM sync_estado WHERE clave = 'impresora_elegida'");
      expect(guardada.single['valor'], contains('66:22:A1:3F:00:12'));

      await tester.tap(find.byKey(const Key('boton_imprimir_prueba')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('Se mandó la prueba'), findsOneWidget);
      // Lo que llegó a la impresora: la prueba, con el ancho del papel completo
      // y los acentos que la tabla de la impresora sí tiene.
      final enviado = telefono.llamadas.lastWhere((l) => l.method == 'imprimir');
      final vista = decodificar(
          ((enviado.arguments as Map)['bytes'] as Uint8List).toList());
      final texto = vista.renderConMarco();
      expect(texto, contains('P R U E B A')); // en doble ancho
      // Ninguna línea más ancha que el papel.
      expect(vista.lineas.every((l) => l.texto.length <= (l.anchoDoble ? 16 : 32)), isTrue);
      expect(texto, contains('12345678901234567890123456789012'));
      expect(texto, contains('Acentos: á é í ó ú ñ Ñ'));
      expect(texto, contains('Distribuciones SE'));
      expect(vista.desconocidos, isEmpty);
    });

    testWidgets('mover a la derecha: el margen va en los bytes y el ancho se ajusta '
        '(ADR 0002 §141)', (tester) async {
      final telefono = _Telefono();
      _conectar(telefono);
      final base = await montarApp(tester, credencial: credencialDelServidor());
      base.db.execute(
        "INSERT INTO sync_estado (clave, valor) VALUES ('impresora_elegida', ?)",
        ['{"nombre":"Printer001","direccion":"66:22:A1:3F:00:12"}'],
      );
      await entrarCon(tester, pinCorrecto);
      await tocarEnElMenu(tester, const Key('boton_impresora'));
      expect(tester.widget<Text>(find.byKey(const Key('margen_impresora'))).data, '0 letra(s)');

      await tester.tap(find.byKey(const Key('boton_margen_mas')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('boton_margen_mas')));
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.byKey(const Key('margen_impresora'))).data, '2 letra(s)');
      expect(textoQueContiene('quedan 30 letras por renglón'), findsOneWidget);
      // Se queda guardado con la impresora.
      final guardada = base.db.select(
          "SELECT valor FROM sync_estado WHERE clave = 'impresora_elegida'");
      expect(guardada.single['valor'], contains('"margen":2'));

      await tester.ensureVisible(find.byKey(const Key('boton_imprimir_prueba')));
      await tester.tap(find.byKey(const Key('boton_imprimir_prueba')));
      await tester.pumpAndSettle();
      final enviado = ((telefono.llamadas.lastWhere((l) => l.method == 'imprimir').arguments
              as Map)['bytes'] as Uint8List)
          .toList();
      // ESC @ y enseguida GS L de 24 puntos: dos letras.
      expect(enviado.sublist(0, 6), [0x1B, 0x40, 0x1D, 0x4C, 24, 0]);
      final vista = decodificar(enviado, columnas: 30);
      expect(vista.desconocidos, isEmpty);
      expect(vista.render(), contains('123456789012345678901234567890'));
      expect(vista.render(), isNot(contains('1234567890123456789012345678901')));
      for (final l in vista.lineas) {
        expect(l.columnasOcupadas, lessThanOrEqualTo(30), reason: l.texto);
      }

      // Y volver a elegirla de la lista no le borra el margen.
      await tester.ensureVisible(find.byKey(const Key('boton_buscar_impresoras')));
      await tester.tap(find.byKey(const Key('boton_buscar_impresoras')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('emparejada_66:22:A1:3F:00:12')));
      await tester.tap(find.byKey(const Key('emparejada_66:22:A1:3F:00:12')));
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.byKey(const Key('margen_impresora'))).data, '2 letra(s)');
    });

    testWidgets('si no contesta, dice que la revise', (tester) async {
      _conectar(_Telefono(codigoAlImprimir: 'no_conecta'));
      final base = await montarApp(tester, credencial: credencialDelServidor());
      base.db.execute(
        "INSERT INTO sync_estado (clave, valor) VALUES ('impresora_elegida', ?)",
        ['{"nombre":"EC-MP200","direccion":"66:22:A1:3F:00:12"}'],
      );
      await entrarCon(tester, pinCorrecto);
      await tocarEnElMenu(tester, const Key('boton_impresora'));
      await tester.tap(find.byKey(const Key('boton_imprimir_prueba')));
      await tester.pumpAndSettle();
      expect(textoQueContiene('La impresora no contestó'), findsOneWidget);

      // Olvidarla pide una segunda confirmación (ADR 0002 §143): con «No» se queda.
      await tester.ensureVisible(find.byKey(const Key('boton_olvidar_impresora')));
      await tester.tap(find.byKey(const Key('boton_olvidar_impresora')));
      await tester.pumpAndSettle();
      expect(find.text('¿Olvidar la impresora EC-MP200?'), findsOneWidget);
      await tester.tap(find.byKey(const Key('confirmar_quitar_no')));
      await tester.pumpAndSettle();
      expect(find.text('Ninguna elegida'), findsNothing);

      await tester.tap(find.byKey(const Key('boton_olvidar_impresora')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('confirmar_quitar_si')));
      await tester.pumpAndSettle();
      expect(find.text('Ninguna elegida'), findsOneWidget);
    });

    testWidgets('sin nada emparejado lo explica', (tester) async {
      _conectar(_Telefono(emparejadas: {'codigo': 'ok', 'impresoras': []}));
      await montarApp(tester, credencial: credencialDelServidor());
      await entrarCon(tester, pinCorrecto);
      await tocarEnElMenu(tester, const Key('boton_impresora'));
      await tester.tap(find.byKey(const Key('boton_buscar_impresoras')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('sin_emparejadas')), findsOneWidget);
    });
  });
}
