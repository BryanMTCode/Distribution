/// Objetivos y mayoreo en el portal de la oficina (ADR 0002 §104 y §105).
///
/// · Objetivos: la lista con su avance, y uno nuevo de un solo día.
/// · Mayoreo: la pestaña solo con el permiso; una venta con el precio cambiado,
///   su ticket, y el total del periodo.
library;

import 'dart:convert';

import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_app/src/estado/sincronizacion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'ayudas.dart';

/// Un servidor de mentira con lo justo para objetivos y mayoreo.
class _Servidor implements Transporte {
  final List<(String, Map<String, Object?>)> posts = [];
  final List<Map<String, Object?>> objetivos = [
    {
      'id': 'o1', 'clase': 'periodo', 'ruta_id': null, 'ruta': null,
      'desde': '2026-09-24', 'hasta': '2026-09-24', 'dias': 1, 'estado': 'en_curso',
      'objetivo_venta': '3000.00', 'objetivo_visitas': null, 'nota': 'Feria',
      'venta': '1500.00', 'visitas': 4, 'logrado': '50.0', 'esperado': '100.0',
      'semaforo': 'atras',
    },
  ];
  final List<Map<String, Object?>> ventas = [];

  Map<String, Object?> get _objetivos => {
        'objetivos': objetivos,
        'rutas': [
          {'id': 'r1', 'codigo': 'R1101', 'nombre': 'Ruta 1101'},
        ],
        'puede_fijar': true,
        'hoy': '2026-09-24',
      };

  static const _venta = {
    'id': 'v1', 'folio': 'MY-000001', 'estado': 'confirmada', 'total': '2500.00',
    'ticket': '*VENTA DE MAYOREO*\nFolio MY-000001\n*Total: \$2,500.00* (efectivo)',
    'motivo_cancelacion': null,
  };

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    switch (ruta) {
      case '/v1/objetivos':
        return RespuestaHttp(200, jsonEncode(_objetivos));
      case '/v1/mayoreo/opciones':
        return RespuestaHttp(200, jsonEncode({
          'clientes': [
            {'id': 'c1', 'codigo': 'C00001', 'nombre_comercial': 'Abarrotes El Mayorista'},
          ],
          'bodegas': [
            {'id': 'b1', 'codigo': 'BODEGA_PRINCIPAL', 'nombre': 'Bodega principal'},
          ],
        }));
      case '/v1/mayoreo/surtido/b1':
        return RespuestaHttp(200, jsonEncode([
          {
            'producto_id': 'p1', 'sku': 'S-108', 'nombre': 'Costal Minino 15 kilos',
            'unidad_base': 'PZA', 'familia': 'Ganador Minino', 'existencia': '197.000',
            'presentaciones': [
              {'unidad': 'PZA', 'factor': '1.000', 'precio_lista': '676.00'},
            ],
          },
        ]));
      case '/v1/mayoreo':
        return RespuestaHttp(200, jsonEncode({
          'desde': '2026-09-24', 'hasta': '2026-09-24', 'ventas': ventas,
          'total': ventas.isEmpty ? '0.00' : '2500.00',
        }));
    }
    return const RespuestaHttp(503, '{"detail":"no en esta prueba"}');
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    posts.add((ruta, cuerpo));
    if (ruta == '/v1/objetivos') {
      objetivos.add({
        ...objetivos.first, 'id': 'o2', 'ruta_id': cuerpo['ruta_id'], 'ruta': 'R1101 · Ruta 1101',
        'desde': cuerpo['desde'], 'hasta': cuerpo['hasta'],
        'objetivo_venta': cuerpo['objetivo_venta'], 'venta': '0.00', 'logrado': '0.0',
        'estado': 'por_empezar', 'nota': null,
      });
      return RespuestaHttp(201, jsonEncode(_objetivos));
    }
    if (ruta == '/v1/mayoreo') {
      ventas.add({
        'id': 'v1', 'folio': 'MY-000001', 'fecha': '2026-09-24', 'estado': 'confirmada',
        'total': '2500.00', 'forma_pago': 'efectivo', 'cliente': 'Abarrotes El Mayorista',
        'bodega': 'Bodega principal', 'vendida_por': 'Bryan', 'renglones': 1,
        'piezas': '4.000',
      });
      return RespuestaHttp(201, jsonEncode(_venta));
    }
    return const RespuestaHttp(503, '{"detail":"no en esta prueba"}');
  }
}

class _Oficina extends ControladorSesion {
  _Oficina(this.rol, this.permisos);

  final String rol;
  final List<String> permisos;

  @override
  Sesion build() => SesionDeGerencia(
        Perfil(usuarioId: 'a1', codigo: 'ADMIN01', nombre: 'Bryan', rol: rol,
            permisos: permisos),
      );
}

Future<void> _montar(WidgetTester tester, _Servidor servidor,
    {String rol = 'admin', List<String> permisos = const []}) async {
  await montarApp(
    tester,
    extras: [
      tokenProvider.overrideWith((_) => 'token-de-prueba'),
      transporteProvider.overrideWithValue(servidor),
      sesionProvider.overrideWith(() => _Oficina(rol, permisos)),
    ],
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('objetivos: la lista con su avance y uno nuevo de un día', (tester) async {
    final servidor = _Servidor();
    await _montar(tester, servidor);
    await tester.tap(find.byKey(const Key('boton_objetivos')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('pantalla_objetivos')), findsOneWidget);
    expect(find.text('Toda la empresa'), findsOneWidget);
    expect(textoQueContiene(r'$1,500.00 de $3,000.00 · 50.0%'), findsOneWidget);
    expect(textoQueContiene('Feria'), findsOneWidget);

    await tester.tap(find.byKey(const Key('boton_nuevo_objetivo')));
    await tester.pumpAndSettle();
    // El objetivo diario fijo vive en el panel (§110, §111): aquí se dice dónde.
    expect(textoQueContiene('Objetivos → Objetivo diario'), findsOneWidget);
    await tester.tap(find.byKey(const Key('campo_ruta_objetivo')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('R1101 · Ruta 1101').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('objetivo_manana')));
    await tester.pumpAndSettle();
    expect(textoQueContiene('Para el viernes 25 de septiembre'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('campo_venta_objetivo')), '5,000');
    await tester.ensureVisible(find.byKey(const Key('boton_guardar_objetivo')));
    await tester.tap(find.byKey(const Key('boton_guardar_objetivo')));
    await tester.pumpAndSettle();

    expect(servidor.posts.last.$2, {
      'ruta_id': 'r1', 'desde': '2026-09-25', 'hasta': '2026-09-25',
      'objetivo_venta': '5000.00',
    });
    expect(textoQueContiene('Por empezar'), findsOneWidget);
    expect(find.text('R1101 · Ruta 1101'), findsOneWidget);
  });

  testWidgets('sin el permiso, la pestaña de mayoreo no aparece', (tester) async {
    await _montar(tester, _Servidor(), rol: 'gerente',
        permisos: const ['tablero.ver', 'inventario.cargar']);
    await tester.tap(find.byKey(const Key('nav_almacen')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pestana_mayoreo')), findsNothing);
  });

  testWidgets('mayoreo: una venta al precio que se decide, con su ticket', (tester) async {
    final servidor = _Servidor();
    await _montar(tester, servidor);
    await tester.tap(find.byKey(const Key('nav_almacen')));
    await tester.pumpAndSettle();
    // La última pestaña del almacén: en un teléfono angosto hay que deslizar.
    await tester.ensureVisible(find.byKey(const Key('pestana_mayoreo')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('pestana_mayoreo')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('mayoreo_vacio')), findsOneWidget);

    await tester.tap(find.byKey(const Key('boton_nueva_venta_mayoreo')));
    await tester.pumpAndSettle();
    // En el ancho del teléfono de la empresa (Hyundai HYLine Pro, 360).
    tester.view.physicalSize = const Size(360, 760);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('campo_cliente_mayoreo')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Abarrotes El Mayorista').last);
    await tester.pumpAndSettle();

    // Los artículos por familia, plegada: se abre y se agrega.
    await tester.tap(find.byKey(const Key('familia_Ganador Minino')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('agregar_mayoreo_S-108')));
    await tester.pumpAndSettle();
    expect(textoQueContiene(r'Lista: $676.00 por PZA'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('cantidad_mayoreo_S-108')), '4');
    await tester.enterText(find.byKey(const Key('precio_mayoreo_S-108')), '625');
    await tester.pumpAndSettle();
    expect(textoQueContiene(r'Guardar venta · $2,500.00'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byKey(const Key('boton_guardar_mayoreo')));
    await tester.pumpAndSettle();

    expect(servidor.posts.last.$1, '/v1/mayoreo');
    expect(servidor.posts.last.$2['renglones'], [
      {'producto_id': 'p1', 'unidad': 'PZA', 'bultos': '4', 'precio': '625.00'},
    ]);
    expect(find.byKey(const Key('pantalla_ticket_compartible')), findsOneWidget);
    expect(textoQueContiene('MY-000001'), findsWidgets);

    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('venta_mayoreo_MY-000001')), findsOneWidget);
    expect(textoQueContiene(r'$2,500.00 en 1 venta(s)'), findsOneWidget);
  });
}
