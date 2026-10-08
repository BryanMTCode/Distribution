/// El cierre del día en la pantalla (ADR 0002 §82).
///
/// Lo que se prueba aquí es el recorrido completo, como lo hace la gente:
///
/// · El vendedor, sin señal: corte (solo el efectivo; lo que le queda en el
///   camión se calcula y se le muestra) → ticket → solicitar carga → ticket.
///   Los dos viajan por la cola, y el ticket se comparte como texto.
/// · Cuando la oficina acepta, «Mi día» lo dice con el folio.
/// · El gerente, por separado: en «Corte del día» cierra el corte del vendedor;
///   en «Cargas» acepta la carga pedida —corrigiendo un renglón— y comparte el
///   ticket, o la rechaza con motivo. Con el corte abierto, la carga espera.
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
      // El camión no se cuenta: lo que le queda se calcula y se le muestra.
      expect(find.byKey(const Key('queda_SOPA-70G')), findsOneWidget);
      expect(textoQueContiene('240 PZA (10 CAJA)'), findsOneWidget);
      expect(find.byType(TextField), findsNWidgets(2), reason: 'efectivo y notas, nada más');

      // Sin efectivo no se termina.
      await verYTocar(tester, const Key('boton_terminar_corte'));
      expect(textoQueContiene('Escribe cuánto efectivo entregas'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('campo_efectivo_corte')), '2200');
      // Escribir sube la lista hasta el campo: el botón queda abajo, sin construir.
      await tester.scrollUntilVisible(find.byKey(const Key('boton_terminar_corte')), 200,
          scrollable: find.byType(Scrollable).first);
      await verYTocar(tester, const Key('boton_terminar_corte'));

      // El ticket del corte.
      expect(find.byKey(const Key('pantalla_ticket_compartible')), findsOneWidget);
      final ticket = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(ticket, contains('*CORTE DEL DÍA*'));
      expect(ticket, contains(r'Faltan $50.00 contra lo vendido en efectivo.'));
      expect(ticket, contains('*LE QUEDA EN EL CAMIÓN*'));
      expect(ticket, contains('• Sopa de fideo 70 g: 10 CAJA'));
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
      final corte = base.db
          .select("SELECT payload FROM outbox WHERE tipo = 'corte.crear'")
          .single['payload'] as String;
      expect(corte, isNot(contains('conteo')));
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
    Future<void> abrirPestana(WidgetTester tester, Key pestana) async {
      await tester.tap(find.byKey(const Key('nav_almacen')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(pestana));
      await tester.pumpAndSettle();
    }

    testWidgets('en «Corte del día» cierra el corte: lo que le queda, calculado',
        (tester) async {
      final servidor = _ServidorDeCierres();
      await _montarGerente(tester, servidor, _CompartidorDePrueba());
      await abrirPestana(tester, const Key('pestana_cortes'));

      expect(find.byKey(const Key('corte_vendedor_VEND01')), findsOneWidget);
      expect(textoQueContiene(r'faltan $50.00'), findsOneWidget);
      // La carga no está aquí: va en su pestaña.
      expect(find.byKey(const Key('carga_pedida_VEND01')), findsNothing);
      await tester.tap(find.byKey(const Key('corte_vendedor_VEND01')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('pantalla_corte_vendedor')), findsOneWidget);
      expect(textoQueContiene('Traía 0 · cargó 240 · vendió 180'), findsOneWidget);
      expect(textoQueContiene('60 PZA'), findsOneWidget);
      await tester.ensureVisible(find.byKey(const Key('boton_cerrar_corte_vendedor')));
      await tester.tap(find.byKey(const Key('boton_cerrar_corte_vendedor')));
      await tester.pumpAndSettle();

      expect(servidor.posts.single.$1, '/v1/cierres/cortes/k1/cerrar');
      expect(textoQueContiene('Liquidación LQ-000004 cerrada'), findsOneWidget);
      // Cerrado, ya no está por cerrar.
      expect(find.byKey(const Key('corte_vendedor_VEND01')), findsNothing);
    });

    testWidgets('en «Cargas», con el corte abierto la carga espera', (tester) async {
      final servidor = _ServidorDeCierres(corteAbierto: true);
      await _montarGerente(tester, servidor, _CompartidorDePrueba());
      await abrirPestana(tester, const Key('pestana_cargas'));

      expect(textoQueContiene('Espera su corte'), findsOneWidget);
      await tester.tap(find.byKey(const Key('carga_pedida_VEND01')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('espera_corte')), findsOneWidget);
      final boton = tester.widget<ButtonStyleButton>(
        find.byKey(const Key('boton_aceptar_carga')),
      );
      expect(boton.onPressed, isNull);
    });

    testWidgets('en «Cargas» corrige un renglón, acepta y comparte el ticket',
        (tester) async {
      final servidor = _ServidorDeCierres();
      final compartidor = _CompartidorDePrueba();
      await _montarGerente(tester, servidor, compartidor);
      await abrirPestana(tester, const Key('pestana_cargas'));

      expect(find.byKey(const Key('corte_vendedor_VEND01')), findsNothing);
      await tester.tap(find.byKey(const Key('carga_pedida_VEND01')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_carga_pedida')), findsOneWidget);
      await tester.enterText(find.byKey(const Key('aceptar_ATUN-140')), '8');
      await tester.ensureVisible(find.byKey(const Key('boton_aceptar_carga')));
      await tester.tap(find.byKey(const Key('boton_aceptar_carga')));
      await tester.pumpAndSettle();

      final (ruta, cuerpo) = servidor.posts.single;
      expect(ruta, '/v1/cierres/solicitudes/0198a2f4-aaaa-7000-8000-000000000001/aceptar');
      // Solo lo que cambió: lo demás se acepta como se pidió.
      expect(cuerpo, {'bultos': {'p1': '8'}});
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
      await abrirPestana(tester, const Key('pestana_cargas'));
      await tester.tap(find.byKey(const Key('carga_pedida_VEND01')));
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
  _ServidorDeCierres({this.corteAbierto = false});

  /// Si el vendedor todavía debe su corte cuando se ve su carga.
  final bool corteAbierto;
  final List<(String, Map<String, Object?>)> posts = [];
  bool _corteCerrado = false;

  static const _vendedor = {
    'vendedor_id': 'v1', 'vendedor_codigo': 'VEND01', 'vendedor': 'Juan Pérez',
    'camion': 'Camión 01',
  };

  Map<String, Object?> _corte({String estado = 'pendiente'}) => {
        ..._vendedor,
        'solicitud': null,
        'corte_por_cerrar': null,
        'corte': {
          'id': 'k1', 'fecha_operativa': '2026-09-24', 'estado': estado,
          'efectivo_declarado': '2200.00', 'efectivo_esperado': '2250.00',
          'diferencia_efectivo': '-50.00', 'observaciones': null,
          'recibido_en': '2026-09-25T01:30:00Z', 'resuelto_en': null,
          'liquidacion_folio': estado == 'cerrado' ? 'LQ-000004' : null, 'nota': null,
          'renglones': [
            {'producto_id': 'p1', 'sku': 'ATUN-140', 'nombre': 'Atún en agua 140 g',
             'unidad_base': 'PZA', 'traia': '0.000', 'cargada': '240.000',
             'vendida': '180.000', 'merma': '0.000', 'devuelta': '0.000',
             'queda': '60.000'},
          ],
        },
      };

  Map<String, Object?> _solicitud({String estado = 'pendiente', String? aceptadas}) => {
        ..._vendedor,
        'corte': null,
        'corte_por_cerrar': corteAbierto && estado == 'pendiente'
            ? {'id': 'k1', 'fecha_operativa': '2026-09-24'}
            : null,
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
    if (ruta == '/v1/cierres/cortes') {
      return RespuestaHttp(200, jsonEncode({
        'pendientes': [if (!_corteCerrado) _corte()],
        'recientes': [if (_corteCerrado) _corte(estado: 'cerrado')],
      }));
    }
    if (ruta == '/v1/cierres/solicitudes') {
      return RespuestaHttp(200, jsonEncode({'pendientes': [_solicitud()], 'recientes': []}));
    }
    return const RespuestaHttp(503, '{"detail":"no importa aquí"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    if (ruta == '/v1/cierres/cortes/k1/cerrar') {
      _corteCerrado = true;
      return RespuestaHttp(200, jsonEncode({
        ..._corte(estado: 'cerrado'),
        'mensaje': 'Liquidación LQ-000004 cerrada. El camión se queda con lo que '
            'calcula el sistema.',
      }));
    }
    if (ruta.endsWith('/aceptar')) {
      return RespuestaHttp(200, jsonEncode({
        ..._solicitud(estado: 'aceptada', aceptadas: '192.000'),
        'mensaje': 'Carga CG-000013 confirmada para el 25/09.',
      }));
    }
    return RespuestaHttp(200, jsonEncode(_solicitud(estado: 'rechazada')));
  }
}
