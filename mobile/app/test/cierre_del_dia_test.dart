/// El cierre del día en la pantalla (ADR 0002 §82).
///
/// Lo que se prueba aquí es el recorrido completo, como lo hace la gente:
///
/// · El vendedor, sin señal: corte (nada se escribe: lo que entrega y lo que le
///   queda los calcula la app, y él confirma, ADR 0002 §133) → ticket, que se
///   imprime o se manda por WhatsApp → solicitar carga → ticket. Los dos viajan
///   por la cola; ya subido, el corte no se rehace.
/// · Cuando la oficina acepta, «Mi día» lo dice con el folio.
/// · El gerente, por separado: en «Corte del día» cierra el corte del vendedor;
///   en «Cargas» acepta la carga pedida —corrigiendo un renglón— y comparte el
///   ticket, o la rechaza con motivo. Con el corte abierto, la carga espera.
/// · Cada cosa una sola vez y por día (ADR 0002 §124): la carga aceptada va en
///   el renglón de su carga, con su ticket, y el corte cerrado en el de su
///   corte, con lo que entregó; no se repiten en «Resueltas» ni «Cerrados».
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_app/src/datos/impresora.dart';
import 'package:dsd_app/src/estado/carrito.dart';
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
    late ImpresoraSimulada impresora;

    Future<(dynamic, _CompartidorDePrueba)> abrirMiDia(WidgetTester tester) async {
      final compartidor = _CompartidorDePrueba();
      impresora = ImpresoraSimulada(carpeta: Directory.systemTemp.createTempSync('corte'));
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
        extras: [
          compartidorProvider.overrideWithValue(compartidor),
          impresoraProvider.overrideWithValue(impresora),
        ],
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
      // Nada se escribe más que las notas: lo que entrega lo calcula la app.
      expect(find.byType(TextField), findsOneWidget, reason: 'solo las notas');
      expect(textoDe(tester, const Key('corte_efectivo_a_entregar')), r'$2,250.00');

      // Confirmar pide confirmación; «Todavía no» no guarda nada.
      await verYTocar(tester, const Key('boton_terminar_corte'));
      expect(find.byKey(const Key('dialogo_confirmar_corte')), findsOneWidget);
      expect(textoQueContiene(r'Entregas $2,250.00 en efectivo'), findsOneWidget);
      await tester.tap(find.text('Todavía no'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('pantalla_corte_del_dia')), findsOneWidget);
      expect(base.db.select("SELECT 1 FROM outbox WHERE tipo = 'corte.crear'"), isEmpty);

      await verYTocar(tester, const Key('boton_terminar_corte'));
      await tester.tap(find.byKey(const Key('confirmar_corte_si')));
      await tester.pumpAndSettle();

      // El ticket del corte: cuadra, porque lo que entrega es lo vendido.
      expect(find.byKey(const Key('pantalla_ticket_compartible')), findsOneWidget);
      expect(textoQueContiene('Se cierra solo al sincronizar'), findsOneWidget);
      final ticket = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(ticket, contains('*CORTE DEL DÍA*'));
      expect(ticket, contains('Cuadra con lo vendido en efectivo.'));
      expect(ticket, contains('*LE QUEDA EN EL CAMIÓN*'));
      expect(ticket, contains('• Sopa de fideo 70 g: 10 CAJA'));

      // Se imprime, con el mismo texto, y se manda por WhatsApp.
      // La impresora simulada guarda el ticket en un archivo: el toque va con
      // la entrada/salida de verdad.
      await tocarConProgreso(tester, const Key('boton_imprimir_ticket'));
      await tester.pumpAndSettle();
      expect(textoQueContiene('el ticket salió en la impresora'), findsOneWidget);
      final impreso = decodificar(impresora.enviados.single).lineas.map((l) => l.texto);
      expect(impreso, contains('CORTE DEL DIA'));
      expect(impreso, contains(r'$2,250.00'));
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
      expect(corte, contains('"efectivo_declarado":"2250.00"'));

      // En Mi día: hecho, y mientras no suba todavía se puede rehacer.
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(textoQueContiene('falta subirlo'), findsOneWidget);
      expect(find.byKey(const Key('rehacer_corte')), findsOneWidget);
      // Ya subido, se cierra solo y no se rehace.
      base.db.execute('UPDATE cortes_vendedor SET sincronizado = 1');
      final contenedor = ProviderScope.containerOf(
          tester.element(find.byKey(const Key('tarjeta_cierre'))));
      contenedor.read(revisionDelCierreProvider.notifier).state++;
      await tester.pumpAndSettle();
      expect(textoQueContiene('se cierra solo'), findsOneWidget);
      expect(find.byKey(const Key('rehacer_corte')), findsNothing);
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

  group('por familia, como el catálogo (ADR 0002 §101)', () {
    Future<void> abrirConFamilias(WidgetTester tester) async {
      await montarApp(
        tester,
        credencial: credencialDelServidor(),
        sembrar: (base) {
          sembrarEscenarioDeVenta(base);
          sembrarParaCobrar(base);
          sembrarProducto(base, id: 'p-cacahuate', nombre: 'Cacahuate japonés');
          sembrarCarga(base, productoId: 'p-cacahuate', unidadesBase: 30);
          sembrarFamilia(base, id: 'f-sopas', nombre: 'Sopas', orden: 2,
              productos: ['p-sopa']);
          sembrarFamilia(base, id: 'f-botanas', nombre: 'Botanas', orden: 1,
              productos: ['p-cacahuate']);
        },
      );
      await entrarCon(tester, pinCorrecto);
      await tester.tap(find.byKey(const Key('boton_mi_dia')));
      await tester.pumpAndSettle();
    }

    Future<void> verYTocar(WidgetTester tester, Key clave) async {
      await tester.ensureVisible(find.byKey(clave));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(clave));
      await tester.pumpAndSettle();
    }

    testWidgets('solicitar carga: agrupada, en el orden de la oficina, y se pliega',
        (tester) async {
      await abrirConFamilias(tester);
      await verYTocar(tester, const Key('boton_solicitar_carga'));
      final botanas = tester.getTopLeft(find.byKey(const Key('familia_Botanas'))).dy;
      final sopas = tester.getTopLeft(find.byKey(const Key('familia_Sopas'))).dy;
      final cacahuate = tester.getTopLeft(find.text('Cacahuate japonés')).dy;
      expect(botanas < cacahuate && cacahuate < sopas, isTrue);

      // Lo escrito en una familia plegada no se pierde.
      await tester.enterText(find.byKey(const Key('pedido_SOPA-70G')), '4');
      await verYTocar(tester, const Key('familia_Sopas'));
      expect(find.byKey(const Key('pedido_SOPA-70G')), findsNothing);
      await verYTocar(tester, const Key('familia_Sopas'));
      expect(
        tester.widget<TextField>(find.byKey(const Key('pedido_SOPA-70G'))).controller!.text,
        '4',
      );
      expect(find.byKey(const Key('plegar_familias')), findsOneWidget);
    });

    testWidgets('solicitar carga cabe en el Hyundai HYLine Pro (360 de ancho)',
        (tester) async {
      await abrirConFamilias(tester);
      await verYTocar(tester, const Key('boton_solicitar_carga'));
      tester.view.physicalSize = const Size(360, 760);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.byKey(const Key('pedido_SOPA-70G')), findsOneWidget);
    });

    testWidgets('el corte dice lo que queda por familia', (tester) async {
      await abrirConFamilias(tester);
      await verYTocar(tester, const Key('boton_corte_del_dia'));
      expect(find.byKey(const Key('familia_Botanas')), findsOneWidget);
      expect(find.byKey(const Key('familia_Sopas')), findsOneWidget);
      final botanas = tester.getTopLeft(find.byKey(const Key('familia_Botanas'))).dy;
      final sopa = tester.getTopLeft(find.byKey(const Key('queda_SOPA-70G'))).dy;
      expect(botanas < sopa, isTrue);
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

      // El efectivo lo cuenta el gerente: arranca en lo declarado (ADR 0002 §87).
      final campo = find.byKey(const Key('campo_efectivo_recibido'));
      expect(tester.widget<TextField>(campo).controller!.text, '2200.00');
      expect(textoQueContiene(r'faltan $50.00. Lo que falta se carga'), findsOneWidget);
      await tester.enterText(campo, '2,100');
      await tester.pumpAndSettle();
      expect(textoQueContiene(r'faltan $150.00. Lo que falta se carga'), findsOneWidget);

      await tester.ensureVisible(find.byKey(const Key('boton_cerrar_corte_vendedor')));
      await tester.tap(find.byKey(const Key('boton_cerrar_corte_vendedor')));
      await tester.pumpAndSettle();

      expect(servidor.posts.single.$1, '/v1/cierres/cortes/k1/cerrar');
      expect(servidor.posts.single.$2, {'efectivo_recibido': '2100.00'});
      expect(textoQueContiene('Liquidación LQ-000004 cerrada'), findsOneWidget);
      // Cerrado, ya no está por cerrar, y dice lo que se recibió.
      expect(find.byKey(const Key('corte_vendedor_VEND01')), findsNothing);
      expect(textoQueContiene(r'Entregó $2,100.00 de $2,250.00 (declaró $2,200.00)'),
          findsOneWidget);
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

    // «En la app de cargas me aparece en resueltas esta semana y abiertas la
    // misma carga» / «quiero que las cargas y cortes se vean por día».
    testWidgets('en «Cargas» la carga aceptada sale una vez, en su día, con su ticket',
        (tester) async {
      final servidor = _ServidorDeCierres(conListas: true);
      final compartidor = _CompartidorDePrueba();
      await _montarGerente(tester, servidor, compartidor);
      await abrirPestana(tester, const Key('pestana_cargas'));

      // Por día, del más nuevo al más viejo.
      final dia25 = find.byKey(const Key('dia_de_cargas_2026-09-25'));
      final dia24 = find.byKey(const Key('dia_de_cargas_2026-09-24'));
      expect(dia25, findsOneWidget);
      expect(dia24, findsOneWidget);
      expect(tester.getTopLeft(dia25).dy, lessThan(tester.getTopLeft(dia24).dy));
      // La aceptada no se repite en «Resueltas esta semana»: va en su carga.
      expect(find.byKey(const Key('carga_pedida_reciente_$_aceptada')), findsNothing);
      expect(find.byKey(const Key('carga_CG-000013')), findsOneWidget);
      expect(find.text('Carga CG-000013'), findsNothing);
      expect(textoQueContiene('la pidió el vendedor'), findsOneWidget);
      // La rechazada no tiene carga: esa sí queda arriba.
      expect(find.byKey(const Key('carga_pedida_reciente_$_rechazada')), findsOneWidget);
      expect(textoQueContiene('Rechazada: No hay atún'), findsOneWidget);
      // La que se hizo a mano no trae ticket.
      expect(find.byKey(const Key('ticket_carga_CG-000012')), findsNothing);

      await tester.ensureVisible(find.byKey(const Key('ticket_carga_CG-000013')));
      await tester.tap(find.byKey(const Key('ticket_carga_CG-000013')));
      await tester.pumpAndSettle();
      final t = tester.widget<SelectableText>(find.byKey(const Key('texto_ticket'))).data!;
      expect(t, contains('*CARGA ACEPTADA*'));
      expect(t, contains('CG-000013'));
      await tester.tap(find.byKey(const Key('boton_compartir_ticket')));
      await tester.pumpAndSettle();
      expect(compartidor.compartidos.single, t);
    });

    testWidgets('en «Corte del día» el corte cerrado sale una vez, en su día',
        (tester) async {
      final servidor = _ServidorDeCierres(conListas: true);
      await _montarGerente(tester, servidor, _CompartidorDePrueba());
      await abrirPestana(tester, const Key('pestana_cortes'));
      await tester.tap(find.byKey(const Key('corte_vendedor_VEND01')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('boton_cerrar_corte_vendedor')));
      await tester.tap(find.byKey(const Key('boton_cerrar_corte_vendedor')));
      await tester.pumpAndSettle();

      // Ya no en «Cerrados esta semana»: en su corte, con lo que entregó.
      expect(find.byKey(const Key('corte_vendedor_reciente_k1')), findsNothing);
      expect(find.text('Cerrados esta semana'), findsNothing);
      await tester.scrollUntilVisible(find.byKey(const Key('corte_LQ-000004')), 200,
          scrollable: find.byType(Scrollable).last);
      expect(
        textoQueContiene(r'Lo mandó el vendedor · Entregó $2,100.00 de $2,250.00'),
        findsOneWidget,
      );
      // Por día, del más nuevo al más viejo.
      final dia24 = find.byKey(const Key('dia_de_cortes_2026-09-24'));
      final dia23 = find.byKey(const Key('dia_de_cortes_2026-09-23'));
      await tester.scrollUntilVisible(dia23, 200, scrollable: find.byType(Scrollable).last);
      expect(tester.getTopLeft(dia24).dy, lessThan(tester.getTopLeft(dia23).dy));
      expect(textoQueContiene('2 corte(s)'), findsOneWidget);
    });

    testWidgets('cargas y cortes por día caben en el Hyundai HYLine Pro (360 de ancho)',
        (tester) async {
      final servidor = _ServidorDeCierres(conListas: true);
      await _montarGerente(tester, servidor, _CompartidorDePrueba());
      tester.view.physicalSize = const Size(360, 760);
      await abrirPestana(tester, const Key('pestana_cargas'));
      expect(tester.takeException(), isNull);
      expect(find.byKey(const Key('carga_CG-000013')), findsOneWidget);
      await tester.tap(find.byKey(const Key('pestana_cortes')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.byKey(const Key('seccion_cortes_vendedores')), findsOneWidget);
    });
  });
}

const _aceptada = '0198a2f4-aaaa-7000-8000-000000000002';
const _rechazada = '0198a2f4-aaaa-7000-8000-000000000003';

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
  _ServidorDeCierres({this.corteAbierto = false, this.conListas = false});

  /// Si el vendedor todavía debe su corte cuando se ve su carga.
  final bool corteAbierto;

  /// Con las listas de abajo —cargas abiertas y cortes— y lo resuelto esta
  /// semana: una carga aceptada y una rechazada (ADR 0002 §124).
  final bool conListas;
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
          'efectivo_declarado': '2200.00',
          'efectivo_recibido': estado == 'cerrado' ? '2100.00' : null,
          'efectivo_esperado': '2250.00',
          'diferencia_efectivo': estado == 'cerrado' ? '-150.00' : '-50.00',
          'observaciones': null,
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

  Map<String, Object?> _solicitud({
    String estado = 'pendiente',
    String? aceptadas,
    String id = '0198a2f4-aaaa-7000-8000-000000000001',
    String? motivo,
  }) => {
        ..._vendedor,
        'corte': null,
        'corte_por_cerrar': corteAbierto && estado == 'pendiente'
            ? {'id': 'k1', 'fecha_operativa': '2026-09-24'}
            : null,
        'solicitud': {
          'id': id, 'fecha_operativa': '2026-09-25',
          'estado': estado, 'observaciones': null, 'recibido_en': '2026-09-25T01:31:00Z',
          'resuelta_en': null, 'resuelta_por': null, 'motivo': motivo,
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
      return RespuestaHttp(200, jsonEncode({
        'pendientes': [_solicitud()],
        'recientes': [
          if (conListas) ...[
            _solicitud(estado: 'aceptada', aceptadas: '240.000', id: _aceptada),
            _solicitud(estado: 'rechazada', id: _rechazada, motivo: 'No hay atún'),
          ],
        ],
      }));
    }
    if (conListas && ruta == '/v1/cargas') {
      Map<String, Object?> carga(String folio, String dia, String estado) => {
            'id': folio, 'folio': folio, 'estado': estado, 'fecha_operativa': dia,
            'vendedor': 'Juan Pérez', 'camion': 'Camión 01', 'renglones': 1,
            'piezas': '240.000',
          };
      return RespuestaHttp(200, jsonEncode([
        carga('CG-000013', '2026-09-25', 'confirmada'),
        carga('CG-000012', '2026-09-24', 'en_ruta'),
      ]));
    }
    if (conListas && ruta == '/v1/cortes') {
      Map<String, Object?> corte(String id, String folio, String dia, String vendedor) => {
            'id': id, 'folio': folio, 'estado': 'cerrada', 'fecha_operativa': dia,
            'vendedor': vendedor, 'carga': 'CG-000012', 'efectivo_esperado': '2250.00',
            'efectivo_entregado': '2100.00', 'diferencia_efectivo': '0.00',
            'faltantes': 0, 'cerrada_en': null,
          };
      return RespuestaHttp(200, jsonEncode({
        'por_cortar': <Object?>[],
        'cortes': [
          if (_corteCerrado) corte('l4', 'LQ-000004', '2026-09-24', 'Juan Pérez'),
          corte('l3', 'LQ-000003', '2026-09-24', 'Ana López'),
          corte('l2', 'LQ-000002', '2026-09-23', 'Juan Pérez'),
        ],
      }));
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
