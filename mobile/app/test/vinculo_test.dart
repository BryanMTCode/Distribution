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
import 'package:dsd_app/src/estado/sesion.dart';
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
  await tester.enterText(
    find.byKey(const Key('campo_password_vinculo')),
    'lo-que-sea',
  );
  await tester.enterText(find.byKey(const Key('campo_equipo_vinculo')), _idEquipo);
  await tester.tap(find.byKey(const Key('boton_vincular')));
  await tester.pumpAndSettle();
}
