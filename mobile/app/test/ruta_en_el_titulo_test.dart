/// La ruta del vendedor arriba, en vez de «Mi ruta» (ADR 0002 §143).
///
/// «Arriba, donde dice Mi ruta, cámbiala por Ruta 1101 o la que tengan
/// asignada.» La ruta viaja en la credencial del teléfono, que se pone al día al
/// entrar y al renovar el acceso.
library;

import 'dart:convert';

import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/datos/repo_credencial.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

Map<String, Object?> _conRutas(List<Map<String, String>> rutas) =>
    {...credencialDelServidor(), 'rutas': rutas};

/// El servidor que renueva el acceso y manda la credencial con otra ruta.
class _Servidor implements Transporte {
  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async => RespuestaHttp(
        200,
        jsonEncode({
          'access_token': 'token-nuevo',
          'refresh_token': 'refresh-viejo',
          'expira_en_seg': 900,
          'credencial_local': _conRutas([
            {'codigo': 'R1102', 'nombre': 'Ruta 1102'},
          ]),
        }),
      );

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      const RespuestaHttp(200, '{}');
}

void main() {
  testWidgets('arriba dice la ruta que trae', (tester) async {
    await montarApp(tester, credencial: _conRutas([
      {'codigo': 'R1101', 'nombre': 'Ruta 1101'},
    ]));
    await entrarCon(tester, pinCorrecto);
    expect(tester.widget<Text>(find.byKey(const Key('titulo_ruta'))).data, 'Ruta 1101');
    expect(find.text('Mi ruta'), findsNothing);
  });

  testWidgets('con dos rutas, las dos', (tester) async {
    await montarApp(tester, credencial: _conRutas([
      {'codigo': 'R1101', 'nombre': 'Ruta 1101'},
      {'codigo': 'R1102', 'nombre': 'Ruta 1102'},
    ]));
    await entrarCon(tester, pinCorrecto);
    expect(tester.widget<Text>(find.byKey(const Key('titulo_ruta'))).data,
        'Ruta 1101 · Ruta 1102');
  });

  testWidgets('una credencial de antes, sin rutas, sigue diciendo «Mi ruta»',
      (tester) async {
    await montarApp(tester, credencial: credencialDelServidor());
    await entrarCon(tester, pinCorrecto);
    expect(tester.widget<Text>(find.byKey(const Key('titulo_ruta'))).data, 'Mi ruta');
  });

  test('si la oficina le cambia la ruta, al renovar el acceso la trae', () async {
    final almacen = AlmacenSeguroEnMemoria();
    await almacen.escribir('credencial_local_v1', jsonEncode(_conRutas([
      {'codigo': 'R1101', 'nombre': 'Ruta 1101'},
    ])));
    await almacen.escribir(claveRefreshToken, 'refresh-viejo');
    final elRef = Provider<Ref>((ref) => ref);
    final contenedor = ProviderContainer(overrides: [
      almacenSeguroProvider.overrideWithValue(almacen),
      transporteSinSesionProvider.overrideWithValue(_Servidor()),
    ]);
    addTearDown(contenedor.dispose);

    expect(await renovarAcceso(contenedor.read(elRef)), isTrue);
    final guardada = await RepoCredencial(almacen).leer();
    expect(guardada!.tituloDeLaRuta, 'Ruta 1102');
  });
}
