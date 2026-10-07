/// La sesión se recuerda al cerrar la app, y vence.
///
/// Reportado en operación (octubre 2026): «si cierro la app y la abro no quiero
/// tener que iniciar sesión de nuevo, que haya un tiempo». Android cierra la app
/// en cuanto el vendedor abre la cámara o el WhatsApp; cada vuelta pedía la
/// contraseña. Y Gerencia, además, perdía el acceso a la media hora porque el
/// token del servidor vencía y nadie lo renovaba.
library;

import 'dart:convert';

import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/datos/transporte_renovable.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

final _manana = DateTime.utc(2026, 9, 24, 13); // 06:00 en Mazatlán

/// Cierra la app y la vuelve a abrir: un árbol nuevo, el mismo Keystore.
Future<void> reabrirApp(
  WidgetTester tester,
  AlmacenSeguroEnMemoria almacen, {
  required DateTime ahora,
}) async {
  await tester.pumpWidget(const SizedBox());
  await montarApp(tester, almacenPropio: almacen, ahora: ahora);
}

/// Transporte del tablero que contesta con lo mínimo; solo importa que no salga
/// a la red de verdad.
class _TransporteMudo implements Transporte {
  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      const RespuestaHttp(503, '{"detail":"sin servidor en la prueba"}');

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      const RespuestaHttp(503, '{}');
}

void main() {
  group('el vendedor', () {
    testWidgets('cierra la app y al abrirla sigue dentro, sin contraseña',
        (tester) async {
      final almacen = AlmacenSeguroEnMemoria();
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        almacenPropio: almacen,
        ahora: _manana,
      );
      await entrarCon(tester, pinCorrecto);
      expect(find.text('Mi ruta'), findsOneWidget);

      // Tres horas después, Android la había cerrado.
      await reabrirApp(tester, almacen, ahora: _manana.add(const Duration(hours: 3)));

      expect(find.text('Mi ruta'), findsOneWidget);
      expect(find.byKey(const Key('campo_pin')), findsNothing);
    });

    testWidgets('pasadas las horas de vigencia, vuelve a pedir la contraseña',
        (tester) async {
      final almacen = AlmacenSeguroEnMemoria();
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        almacenPropio: almacen,
        ahora: _manana,
      );
      await entrarCon(tester, pinCorrecto);

      await reabrirApp(
        tester,
        almacen,
        ahora: _manana.add(vigenciaDeLaSesion + const Duration(minutes: 1)),
      );

      expect(find.byKey(const Key('campo_pin')), findsOneWidget);
      expect(find.text('Mi ruta'), findsNothing);
      // Y no queda nada recordado que otra apertura pudiera revivir.
      expect(await almacen.leer(claveSesionRecordada), isNull);
    });

    testWidgets('salir la olvida: al abrir otra vez pide la contraseña',
        (tester) async {
      final almacen = AlmacenSeguroEnMemoria();
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        almacenPropio: almacen,
        ahora: _manana,
      );
      await entrarCon(tester, pinCorrecto);
      await tocarEnElMenu(tester, const Key('boton_salir'));

      await reabrirApp(tester, almacen, ahora: _manana.add(const Duration(minutes: 5)));

      expect(find.byKey(const Key('campo_pin')), findsOneWidget);
    });

    testWidgets('una credencial vencida no entra por la puerta de atrás',
        (tester) async {
      // La sesión recordada no salta la vigencia de la credencial: pasada esa
      // fecha hay que conectarse, que es cuando el servidor puede decir que el
      // equipo está dado de baja.
      final almacen = AlmacenSeguroEnMemoria();
      await almacen.escribir(
        'credencial_local_v1',
        jsonEncode(credencialDelServidor(validaHasta: '2026-09-24T14:00:00.000Z')),
      );
      await almacen.escribir(
        claveSesionRecordada,
        jsonEncode({'tipo': 'vendedor', 'hasta': '2026-09-24T23:00:00.000Z'}),
      );

      await montarApp(tester, almacenPropio: almacen, ahora: DateTime.utc(2026, 9, 24, 15));

      expect(find.byKey(const Key('campo_pin')), findsOneWidget);
    });
  });

  group('gerencia', () {
    Future<AlmacenSeguroEnMemoria> conGerenciaRecordada({required String hasta}) async {
      final almacen = AlmacenSeguroEnMemoria();
      await almacen.escribir(claveRefreshToken, 'refresh-de-prueba');
      await almacen.escribir(
        claveSesionRecordada,
        jsonEncode({
          'tipo': 'gerencia',
          'hasta': hasta,
          'perfil': {
            'usuario_id': 'g1',
            'codigo': 'GER01',
            'nombre': 'Bryan Gerencia',
            'rol': 'gerente',
            'permisos': ['tablero.ver'],
          },
        }),
      );
      return almacen;
    }

    testWidgets('al abrir la app entra directo al tablero', (tester) async {
      final almacen = await conGerenciaRecordada(hasta: '2026-09-24T20:00:00.000Z');
      await montarApp(
        tester,
        almacenPropio: almacen,
        ahora: _manana,
        extras: [transporteProvider.overrideWithValue(_TransporteMudo())],
      );

      expect(find.byKey(const Key('panel_gerencia')), findsOneWidget);
      expect(find.byKey(const Key('campo_pin')), findsNothing);
    });

    testWidgets('vencida, pide entrar y borra el refresh token', (tester) async {
      final almacen = await conGerenciaRecordada(hasta: '2026-09-24T10:00:00.000Z');
      await montarApp(tester, almacenPropio: almacen, ahora: _manana);

      expect(find.byKey(const Key('panel_gerencia')), findsNothing);
      expect(find.byKey(const Key('campo_pin')), findsOneWidget);
      expect(await almacen.leer(claveRefreshToken), isNull);
    });
  });

  group('el acceso se renueva solo', () {
    test('un 401 renueva y repite la petición con el token nuevo', () async {
      var token = 'viejo';
      final interno = _TransporteQueExigeToken(() => token, valido: 'nuevo');
      var renovaciones = 0;
      final transporte = TransporteRenovable(interno, renovar: () async {
        renovaciones++;
        token = 'nuevo';
        return true;
      });

      final r = await transporte.obtener('/v1/tablero');

      expect(r.codigo, 200);
      expect(renovaciones, 1);
      expect(interno.tokensVistos, ['viejo', 'nuevo']);
    });

    test('si no se puede renovar, devuelve el 401 y no repite', () async {
      final interno = _TransporteQueExigeToken(() => 'viejo', valido: 'nuevo');
      final transporte = TransporteRenovable(interno, renovar: () async => false);

      final r = await transporte.post('/v1/sync/push', const {});

      expect(r.codigo, 401);
      expect(interno.tokensVistos, ['viejo']);
    });

    test('dos 401 a la vez hacen UNA renovación', () async {
      var token = 'viejo';
      final interno = _TransporteQueExigeToken(() => token, valido: 'nuevo');
      var renovaciones = 0;
      final transporte = TransporteRenovable(interno, renovar: () async {
        renovaciones++;
        await Future<void>.delayed(const Duration(milliseconds: 10));
        token = 'nuevo';
        return true;
      });

      final respuestas = await Future.wait([
        transporte.obtener('/v1/tablero'),
        transporte.obtener('/v1/tablero/mapa'),
      ]);

      expect(respuestas.map((r) => r.codigo), [200, 200]);
      expect(renovaciones, 1);
    });

    test('lo que no es 401 pasa tal cual, sin renovar', () async {
      final transporte = TransporteRenovable(
        _TransporteMudo(),
        renovar: () async => fail('no debía renovar'),
      );
      expect((await transporte.obtener('/v1/tablero')).codigo, 503);
    });
  });
}

class _TransporteQueExigeToken implements Transporte {
  _TransporteQueExigeToken(this._token, {required this.valido});

  final String Function() _token;
  final String valido;
  final List<String> tokensVistos = [];

  RespuestaHttp _responder() {
    final t = _token();
    tokensVistos.add(t);
    return t == valido
        ? const RespuestaHttp(200, '{}')
        : const RespuestaHttp(401, '{"detail":"token vencido"}');
  }

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      _responder();

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async => _responder();
}
