/// El cierre del día en la pantalla (ADR 0002 §82).
///
/// Lo que se prueba aquí es el recorrido completo, como lo hace la gente:
///
/// · El vendedor, sin señal: corte (conteo a ciegas + efectivo) → ticket →
///   solicitar carga → ticket. Los dos viajan por la cola, y el ticket se
///   comparte como texto.
/// · Cuando la oficina acepta, «Mi día» lo dice con el folio.
/// · El gerente: ve el corte junto con la carga pedida, corrige un renglón,
///   acepta y comparte el ticket de la carga; o la rechaza con motivo.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/cierre.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

class _CompartidorDePrueba implements Compartidor {
  final List<String> compartidos = [];

  @override
  Future<void> compartir(String texto, {String? asunto}) async => compartidos.add(texto);
}

void main() {
  group('el vendedor', () {
    Future<(dynamic, _CompartidorDePrueba)> abrirMiDia(WidgetTester tester) async {
      final compartidor = _CompartidorDePrueba();
      final base = await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          sembrarEscenarioDeVenta(base);
          sembrarParaCobrar(base);
          sembrarVentaDelDia(base, folio: 'V1', total: 2250, cliente: 'cliente-1');
          sembrarVentaDelDia(base, folio: 'V2', total: 500, cliente: 'cliente-1',
              formaPago: 'transferencia');
        },
        extras: [compartidorProvider.overrideWithValue(compartidor)],
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_mi_dia')));
      await tester.pumpAndSettle();
      return (base, compartidor);
    }

    Future<void> verYTocar(WidgetTester tester, Key clave) async {
      await tester.ensureVisible(find.byKey(clave));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(clave));
      await tester.pumpAndSettle();
    }

    testWidgets('corte → ticket → solicitar carga → ticket, todo sin señal', (tester) async {
      final (base, compartidor) = await abrirMiDia(tester);
      await verYTocar(tester, const Key('boton_corte_del_dia'));
      expect(find.byKey(const Key('pantalla_corte_del_dia')), findsOneWidget);
      expect(find.byKey(const Key('corte_efectivo_vendido')), findsOneWidget);
      expect(textoQueContiene(r'En efectivo: $2,250.00'), findsOneWidget);
      // El conteo es a ciegas: no se ve cuánto cree el sistema que trae.
      expect(textoQueContiene('240'), findsNothing);

      // Sin contar no se termina.
      await tester.enterText(find.byKey(const Key('campo_efectivo_corte')), '2200');
      await verYTocar(tester, const Key('boton_terminar_corte'));
      expect(textoQueContiene('Falta contar: Sopa de fideo 70 g'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('conteo_SOPA-70G')), '230');
      await verYTocar(tester, const Key('boton_terminar_corte'));

      // El ticket del corte.
      expect(find.byKey(const Key('pantalla_ticket_compartible')), findsOneWidget);
      final ticket = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(ticket, contains('*CORTE DEL DÍA*'));
      expect(ticket, contains(r'Faltan $50.00 contra lo vendido en efectivo.'));
      expect(ticket, contains('• Sopa de fideo 70 g: 230 PZA'));
      await verYTocar(tester, const Key('boton_compartir_ticket'));
      expect(compartidor.compartidos.single, ticket);

      // Y de ahí, la carga de mañana.
      await verYTocar(tester, const Key('boton_siguiente_ticket'));
      expect(find.byKey(const Key('pantalla_solicitar_carga')), findsOneWidget);
      expect(textoQueContiene('Para el viernes 25 de septiembre'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('pedido_SOPA-70G')), '10');
      await tester.pumpAndSettle();
      await verYTocar(tester, const Key('boton_enviar_solicitud'));

      final carga = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(carga, contains('*SOLICITUD DE CARGA*'));
      expect(carga, contains('• Sopa de fideo 70 g: 10 CAJA'));

      // Los dos documentos van en la cola, en orden.
      final tipos = base.db
          .select('SELECT tipo FROM outbox ORDER BY secuencia')
          .map((f) => f['tipo'] as String)
          .toList();
      expect(tipos, containsAllInOrder(['corte.crear', 'solicitud_carga.crear']));
    });

    testWidgets('cuando la oficina acepta, Mi día dice el folio de la carga', (tester) async {
      final (base, _) = await abrirMiDia(tester);
      await verYTocar(tester, const Key('boton_solicitar_carga'));
      await tester.enterText(find.byKey(const Key('pedido_SOPA-70G')), '3');
      await tester.pumpAndSettle();
      await verYTocar(tester, const Key('boton_enviar_solicitud'));
      await tester.pageBack();
      await tester.pumpAndSettle();

      expect(textoQueContiene('Carga de mañana pedida'), findsOneWidget);

      // Llega la respuesta de la oficina con la sincronización.
      final id = base.db.select('SELECT id FROM solicitudes_carga').single['id'] as String;
      AplicadorDeltas(base.db).aplicar([
        Delta(
          cursor: 50,
          entidad: 'solicitud_carga',
          entidadId: id,
          operacion: 'upsert',
          payload: {
            'estado': 'aceptada',
            'fecha_operativa': '2026-09-25',
            'carga_folio': 'CG-000031',
            'detalle': [
              {'producto_id': 'p-sopa', 'unidad_codigo': 'CAJA', 'bultos': '3.000',
               'cantidad': '72.000', 'cantidad_aceptada': '72.000'},
            ],
          },
        ),
      ], recibidoEn: '2026-09-25T01:00:00.000Z');
      final contenedor = tester.element(find.byKey(const Key('tarjeta_cierre')));
      ProviderScope.containerOf(contenedor).read(revisionDelCamionProvider.notifier).state++;
      await tester.pumpAndSettle();

      expect(textoQueContiene('Carga de mañana ACEPTADA: CG-000031'), findsOneWidget);
      await verYTocar(tester, const Key('ver_ticket_carga'));
      final t = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(t, contains('*CARGA ACEPTADA*'));
      expect(t, contains('Carga CG-000031'));
    });
  });

  group('el gerente', () {
    testWidgets('ve el corte con la carga, corrige un renglón, acepta y comparte',
        (tester) async {
      final servidor = _ServidorDeCierres();
      final compartidor = _CompartidorDePrueba();
      await _montarGerente(tester, servidor, compartidor);

      await tester.tap(find.byKey(const Key('nav_almacen')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('cierre_VEND01')), findsOneWidget);
      expect(textoQueContiene(r'faltan $50.00'), findsOneWidget);
      await tester.tap(find.byKey(const Key('cierre_VEND01')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_cierre')), findsOneWidget);
      expect(textoQueContiene('NO LO CONTÓ'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('aceptar_ATUN-140')), '8');
      await tester.ensureVisible(find.byKey(const Key('boton_aceptar_cierre')));
      await tester.tap(find.byKey(const Key('boton_aceptar_cierre')));
      await tester.pumpAndSettle();

      final (ruta, cuerpo) = servidor.posts.single;
      expect(ruta, '/v1/cierres/aceptar');
      expect(cuerpo, {
        'corte_id': 'k1',
        'solicitud_id': '0198a2f4-aaaa-7000-8000-000000000001',
        // Solo lo que cambió: lo demás se acepta como se pidió.
        'bultos': {'p1': '8'},
      });
      final t = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(t, contains('*CARGA ACEPTADA*'));
      expect(t, contains('• Atún en agua 140 g: 8 CAJA (pidió 10 CAJA)'));
      expect(textoQueContiene('Carga CG-000013 confirmada'), findsOneWidget);
      await tester.tap(find.byKey(const Key('boton_compartir_ticket')));
      await tester.pumpAndSettle();
      expect(compartidor.compartidos.single, t);
    });

    testWidgets('rechaza con motivo', (tester) async {
      final servidor = _ServidorDeCierres();
      await _montarGerente(tester, servidor, _CompartidorDePrueba());
      await tester.tap(find.byKey(const Key('nav_almacen')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cierre_VEND01')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('boton_rechazar_solicitud')));
      await tester.tap(find.byKey(const Key('boton_rechazar_solicitud')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('campo_motivo_rechazo')), 'Mañana descansas');
      await tester.tap(find.byKey(const Key('boton_rechazar_de_verdad')));
      await tester.pumpAndSettle();
      expect(servidor.posts.single.$1,
          '/v1/cierres/solicitudes/0198a2f4-aaaa-7000-8000-000000000001/rechazar');
      expect(servidor.posts.single.$2, {'motivo': 'Mañana descansas'});
    });
  });
}

class _Gerencia extends ControladorSesion {
  @override
  Sesion build() => const SesionDeGerencia(
        Perfil(usuarioId: 'g1', codigo: 'GER01', nombre: 'Bryan', rol: 'gerente',
            permisos: ['tablero.ver', 'inventario.cargar', 'inventario.liquidar']),
      );
}

Future<void> _montarGerente(
  WidgetTester tester,
  _ServidorDeCierres servidor,
  _CompartidorDePrueba compartidor,
) async {
  await montarApp(
    tester,
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(servidor),
      sesionProvider.overrideWith(_Gerencia.new),
      compartidorProvider.overrideWithValue(compartidor),
    ],
  );
  await tester.pumpAndSettle();
}

class _ServidorDeCierres implements Transporte {
  final List<(String, Map<String, Object?>)> posts = [];

  Map<String, Object?> _cierre({String estado = 'pendiente', String? aceptadas}) => {
        'vendedor_id': 'v1', 'vendedor_codigo': 'VEND01', 'vendedor': 'Juan Pérez',
        'camion': 'Camión 01',
        'corte': {
          'id': 'k1', 'fecha_operativa': '2026-09-24',
          'estado': estado == 'pendiente' ? 'pendiente' : 'cerrado',
          'efectivo_declarado': '2200.00', 'efectivo_esperado': '2250.00',
          'diferencia_efectivo': '-50.00', 'observaciones': null,
          'recibido_en': '2026-09-25T01:30:00Z', 'resuelto_en': null,
          'liquidacion_folio': null, 'nota': null,
          'renglones': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'contada': '55.000', 'contado': true,
             'sistema': '60.000', 'diferencia': '-5.000'},
            {'producto_id': 'p2', 'sku': 'GALL', 'nombre': 'Galletas', 'unidad_base': 'PZA',
             'contada': '0.000', 'contado': false, 'sistema': '4.000',
             'diferencia': '-4.000'},
          ],
        },
        'solicitud': {
          'id': '0198a2f4-aaaa-7000-8000-000000000001', 'fecha_operativa': '2026-09-25',
          'estado': estado, 'observaciones': null, 'recibido_en': '2026-09-25T01:31:00Z',
          'resuelta_en': null, 'resuelta_por': null, 'motivo': null,
          'carga_folio': estado == 'aceptada' ? 'CG-000013' : null, 'bodega': 'Bodega',
          'renglones': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'unidad_codigo': 'CAJA', 'factor': '24.000',
             'bultos': '10.000', 'cantidad': '240.000', 'cantidad_aceptada': aceptadas,
             'en_bodega': '200.000'},
          ],
        },
      };

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    if (ruta == '/v1/cierres') {
      return RespuestaHttp(200, jsonEncode({'pendientes': [_cierre()], 'recientes': []}));
    }
    return const RespuestaHttp(503, '{"detail":"no importa aquí"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    if (ruta == '/v1/cierres/aceptar') {
      return RespuestaHttp(200, jsonEncode({
        ..._cierre(estado: 'aceptada', aceptadas: '192.000'),
        'mensaje': 'Liquidación LQ-000004 cerrada. Carga CG-000013 confirmada para el 25/09.',
      }));
    }
    return RespuestaHttp(200, jsonEncode(_cierre(estado: 'rechazada')));
  }
}
