/// Vincular el teléfono a su vendedor: el paso que no existía.
///
/// Un teléfono recién instalado no tenía NINGÚN camino para entrar. El login sin
/// señal necesita una credencial guardada, y lo único que la guardaba era el
/// sembrador del modo demo — que los dos cerrojos de compilación eliminan del
/// binario de release. Las cinco piezas estaban escritas y ninguna conectada:
///
///   · el servidor devuelve `credencial_local` si el login trae `dispositivo_id`
///   · `ClienteAuth.entrar` acepta el parámetro
///   · `RepoCredencial.guardar` existe
///   · `/dispositivos/{id}/folios` asigna rangos
///   · `RepoFolios.guardar` existe
///
/// Estas pruebas ejercen la unión, y sobre todo el orden: la credencial se
/// guarda ANTES de pedir los folios, porque una credencial perdida no se
/// recupera sin señal y unos folios sí se reintentan.
library;

import 'dart:convert';

import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

const _idEquipo = '019283a0-0003-7000-8000-000000000003';

/// Un servidor que contesta el login y los folios, con fallos a pedir.
class _ServidorDeVinculo implements Transporte {
  _ServidorDeVinculo({
    this.credencial = true,
    this.codigoLogin = 200,
    this.foliosSeCaen = false,
  });

  /// `false` simula un servidor que acepta el login pero no manda credencial —
  /// lo que pasa si el dispositivo no está registrado a nombre de ese vendedor.
  final bool credencial;
  final int codigoLogin;
  final bool foliosSeCaen;
  final List<String> llamadas = [];

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    llamadas.add(ruta);

    if (ruta.endsWith('/folios')) {
      if (foliosSeCaen) throw const ErrorDeRed('sin señal');
      return RespuestaHttp(
        200,
        jsonEncode([
          for (final tipo in ['venta', 'cobro', 'merma', 'no_drop'])
            {
              'documento_tipo': tipo,
              'desde': 1,
              'hasta': 1000,
              'consumido_hasta': 0,
            },
        ]),
      );
    }

    if (codigoLogin != 200) {
      return RespuestaHttp(
        codigoLogin,
        jsonEncode({'detail': 'dispositivo no registrado: regístralo antes'}),
      );
    }

    return RespuestaHttp(
      200,
      jsonEncode({
        'access_token': 'token-de-prueba',
        'refresh_token': 'refresh-de-prueba',
        'expira_en_seg': 900,
        'perfil': {
          'usuario_id': '019283a0-0001-7000-8000-000000000001',
          'codigo': 'VEND01',
          'nombre': 'Juan Pérez',
          'rol': 'vendedor',
          'permisos': ['ventas.crear'],
          'almacen_id': null,
        },
        if (credencial) 'credencial_local': credencialDelServidor(),
      }),
    );
  }

  @override
  Future<RespuestaHttp> obtener(
    String ruta, {
    Map<String, String> parametros = const {},
  }) async {
    llamadas.add(ruta);
    return const RespuestaHttp(200, '{}');
  }
}

List<Override> _con(_ServidorDeVinculo servidor) => [
      transporteSinSesionProvider.overrideWithValue(servidor),
      transporteProvider.overrideWithValue(servidor),
    ];

void main() {
  testWidgets('la pantalla ofrece vincular el equipo', (tester) async {
    await montarApp(tester);
    // Plegado, porque se usa una vez en la vida del equipo: lo de todos los
    // días es el PIN.
    expect(find.byKey(const Key('boton_abrir_vinculo')), findsOneWidget);
    expect(find.byKey(const Key('campo_equipo_vinculo')), findsNothing);

    await tester.tap(find.byKey(const Key('boton_abrir_vinculo')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('campo_equipo_vinculo')), findsOneWidget);
  });

  testWidgets('vincular guarda la credencial y los folios', (tester) async {
    final servidor = _ServidorDeVinculo();
    final almacen = AlmacenSeguroEnMemoria();
    final base = await montarApp(
      tester,
      extras: _con(servidor),
      almacenPropio: almacen,
    );

    await _vincular(tester);

    // 1. La credencial, que es lo que permite entrar sin señal mañana.
    final guardada = await almacen.leer('credencial_local_v1');
    expect(guardada, isNotNull);
    expect(jsonDecode(guardada!)['codigo'], equals('VEND01'));

    // 2. El `dispositivo_id`, que cada documento de campo estampa.
    expect(
      base.db
          .select("SELECT valor FROM sync_estado WHERE clave = 'dispositivo_id'")
          .single['valor'],
      equals(_idEquipo),
    );

    // 3. Los folios: sin ellos el vendedor entra y NO puede cerrar una venta.
    final rangos = base.db.select('SELECT tipo FROM folios_rangos ORDER BY tipo');
    expect(
      rangos.map((f) => f['tipo']),
      containsAll(['cobro', 'merma', 'no_drop', 'venta']),
    );

    // Y el orden de las llamadas: el login primero, los folios después.
    expect(servidor.llamadas.first, equals('/v1/auth/login'));
    expect(servidor.llamadas.last, contains('/folios'));
  });

  testWidgets('el campo del login diario acepta letras, no solo números',
      (tester) async {
    // Bloqueante y silencioso: el campo verifica contra `passwordHash` —la
    // contraseña de la oficina, con letras— y abría el teclado NUMÉRICO. En un
    // teléfono eso significa que la contraseña no se puede escribir, así que el
    // login de todos los días era imposible y el único camino que funcionaba era
    // volver a vincular el equipo con los tres datos, cada vez.
    await montarApp(tester, extras: _con(_ServidorDeVinculo()));

    final campo = tester.widget<TextField>(find.byKey(const Key('campo_pin')));
    expect(campo.keyboardType, equals(TextInputType.text),
        reason: 'con teclado numérico la contraseña del vendedor no se puede '
            'teclear: el login diario queda imposible');
    expect(campo.obscureText, isTrue,
        reason: 'se teclea a la vista de quien esté enfrente en la tienda');
  });

  testWidgets('entrar con la contraseña consigue token para poder sincronizar',
      (tester) async {
    // El fallo: `tokenProvider` lo ponían solo `vincularEquipo` y los caminos de
    // Gerencia. El login de TODOS LOS DÍAS no lo ponía, así que el vendedor
    // entraba bien y al sincronizar la app decía «Entraste sin señal» —con señal
    // de sobra—, porque sin token no intenta. La única salida era volver a
    // vincular el equipo, que sí hace login en línea.
    final servidor = _ServidorDeVinculo();
    await montarApp(tester, extras: _con(servidor));

    await _vincular(tester);
    final loginsTrasVincular =
        servidor.llamadas.where((l) => l == '/v1/auth/login').length;

    // Salir borra el token y el refresh, pero NO la credencial.
    await tester.tap(find.byKey(const Key('boton_salir')));
    await tester.pumpAndSettle();

    // Y entrar como cada mañana: solo la contraseña.
    await tester.enterText(find.byKey(const Key('campo_pin')), pinCorrecto);
    await tester.tap(find.byKey(const Key('boton_entrar')));
    await tester.pumpAndSettle();

    expect(
      servidor.llamadas.where((l) => l == '/v1/auth/login').length,
      greaterThan(loginsTrasVincular),
      reason: 'entrar con la contraseña no pidió token: el vendedor queda dentro '
          'de la app y sin poder subir nada, y la pantalla dirá «sin señal»',
    );
  });

  testWidgets('la pantalla de inicio dice de quién es el teléfono',
      (tester) async {
    // Para que el vendedor no tenga que recordar con qué código quedó vinculado
    // su equipo, y para que la oficina vea de quién es con el teléfono en la mano.
    final servidor = _ServidorDeVinculo();
    await montarApp(tester, extras: _con(servidor));

    expect(find.byKey(const Key('etiqueta_vendedor')), findsNothing,
        reason: 'sin vincular no hay nombre que mostrar, y no se inventa');

    await _vincular(tester);
    await tester.tap(find.byKey(const Key('boton_salir')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('etiqueta_vendedor')), findsOneWidget);
    expect(find.text('Juan Pérez'), findsOneWidget);
    expect(find.text('VEND01'), findsOneWidget);
  });

  testWidgets('vincular ENTRA, no solo guarda', (tester) async {
    // El fallo que esto cierra: vincular guardaba credencial, folios y token
    // correctamente, y la pantalla se limpiaba sin más. El vendedor volvía a la
    // pantalla de entrada sin un mensaje y sin saber si había funcionado — con la
    // contraseña BIEN el resultado era indistinguible de no haber hecho nada, y
    // con la contraseña mal sí veía el error, lo que lo hacía más desconcertante.
    //
    // El botón dice «Vincular y entrar». Esto exige la segunda mitad.
    await montarApp(tester, extras: _con(_ServidorDeVinculo()));

    expect(find.byKey(const Key('boton_abrir_vinculo')), findsOneWidget,
        reason: 'antes de vincular se está en la pantalla de entrada');

    await _vincular(tester);

    expect(find.byKey(const Key('boton_abrir_vinculo')), findsNothing,
        reason: 'después de vincular la app sigue en la pantalla de entrada: '
            'vincular guardó todo y no abrió la sesión');
  });

  testWidgets('si los folios se caen, el equipo YA quedó vinculado',
      (tester) async {
    // Es la decisión de orden: la credencial se guarda antes. Si la red se corta
    // en medio, el vendedor puede entrar con su PIN —le faltarán folios y la
    // pantalla de cobro lo dirá— en vez de quedar fuera de la app. Pedir folios
    // se reintenta con señal; recuperar una credencial perdida, no.
    final almacen = AlmacenSeguroEnMemoria();
    final base = await montarApp(
      tester,
      extras: _con(_ServidorDeVinculo(foliosSeCaen: true)),
      almacenPropio: almacen,
    );

    await _vincular(tester);

    expect(await almacen.leer('credencial_local_v1'), isNotNull);
    expect(base.db.select('SELECT tipo FROM folios_rangos'), isEmpty);
    expect(
      find.textContaining('no se pudieron traer los folios'),
      findsOneWidget,
    );
  });

  testWidgets('un login sin credencial no deja el equipo a medias',
      (tester) async {
    // Pasa si el dispositivo existe pero es de otro usuario, o si el id no
    // corresponde: el servidor acepta y no manda credencial. Sin ella no hay
    // login offline, así que vincular no sirvió y hay que decirlo.
    final almacen = AlmacenSeguroEnMemoria();
    await montarApp(
      tester,
      extras: _con(_ServidorDeVinculo(credencial: false)),
      almacenPropio: almacen,
    );

    await _vincular(tester);

    expect(await almacen.leer('credencial_local_v1'), isNull);
    expect(find.textContaining('no devolvió credencial'), findsOneWidget);
  });

  testWidgets('el mensaje del servidor se muestra tal cual', (tester) async {
    // «dispositivo no registrado» dice qué hacer. Traducirlo a un genérico
    // dejaría a quien lo lea intentando lo mismo otra vez.
    await montarApp(
      tester,
      extras: _con(_ServidorDeVinculo(codigoLogin: 409)),
    );

    await _vincular(tester);

    expect(find.byKey(const Key('aviso_vinculo')), findsOneWidget);
    expect(find.textContaining('dispositivo no registrado'), findsOneWidget);
  });

  testWidgets('sin identificador no se llama al servidor', (tester) async {
    final servidor = _ServidorDeVinculo();
    await montarApp(tester, extras: _con(servidor));

    await tester.tap(find.byKey(const Key('boton_abrir_vinculo')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('campo_codigo_vinculo')),
      'VEND01',
    );
    await tester.enterText(
      find.byKey(const Key('campo_password_vinculo')),
      'lo-que-sea',
    );
    await tester.tap(find.byKey(const Key('boton_vincular')));
    await tester.pumpAndSettle();

    expect(servidor.llamadas, isEmpty);
    expect(find.textContaining('Falta el identificador'), findsOneWidget);
  });
}

Future<void> _vincular(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('boton_abrir_vinculo')));
  await tester.pumpAndSettle();
  await tester.enterText(
    find.byKey(const Key('campo_codigo_vinculo')),
    'VEND01',
  );
  // `pinCorrecto` y no un texto cualquiera: el `password_hash` que manda el
  // servidor falso es el de ESA contraseña, igual que en la realidad. Vincular
  // termina abriendo la sesión con la credencial recién guardada, así que una
  // contraseña que no corresponda al hash haría fallar ese último paso.
  await tester.enterText(
    find.byKey(const Key('campo_password_vinculo')),
    pinCorrecto,
  );
  await tester.enterText(find.byKey(const Key('campo_equipo_vinculo')), _idEquipo);
  await tester.tap(find.byKey(const Key('boton_vincular')));
  await tester.pumpAndSettle();
}
