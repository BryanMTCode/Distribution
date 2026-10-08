/// El cliente de `/v1/vendedores`: lo que la pantalla de la oficina recibe.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.codigo, this.cuerpo);

  final int codigo;
  final Object? cuerpo;
  final List<(String, Map<String, String>?)> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    return RespuestaHttp(codigo, cuerpo is String ? cuerpo! as String : jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async =>
      const RespuestaHttp(405, '{}');
}

const _periodo = {
  'clave': 'hoy', 'etiqueta': 'Hoy', 'desde': '2026-10-07', 'hasta': '2026-10-07',
  'descripcion': 'hoy',
};
const _periodos = [['hoy', 'Hoy'], ['semana', 'Esta semana']];

void main() {
  test('la lista trae el periodo y el dinero exacto', () async {
    final t = _Transporte(200, {
      'periodo': _periodo,
      'periodos': _periodos,
      'vendedores': [
        {
          'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'activo': true,
          'camion': 'Camión 01', 'rutas': 'R04', 'ventas': 3, 'importe': '2250.50',
          'efectivo': '100.00', 'no_ventas': 1, 'mermas': 0,
          'ultimo_contacto': '2026-10-07T15:00:00Z', 'saldo_cuenta': '-12.00',
        },
      ],
    });
    final lista = await ClienteVendedores(t).lista(periodo: 'semana');
    expect(t.pedidas.single.$1, '/v1/vendedores');
    expect(t.pedidas.single.$2, {'periodo': 'semana'});
    expect(lista.periodos.map((p) => p.$1), ['hoy', 'semana']);
    final juan = lista.vendedores.single;
    expect(juan.importe.centavos, 225050);
    expect(juan.saldoCuenta.centavos, -1200);
    expect(juan.ultimoContacto, DateTime.utc(2026, 10, 7, 15));
  });

  test('el detalle trae resumen, movimientos con su id y teléfonos', () async {
    final t = _Transporte(200, {
      'vendedor': {
        'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan Pérez', 'activo': true,
        'camion': 'Camión 01', 'rutas': 'R04 · Centro', 'saldo_cuenta': '0.00',
      },
      'periodo': _periodo,
      'periodos': _periodos,
      'resumen': [
        {'tipo': 'venta', 'etiqueta': 'Ventas', 'cuantos': 1, 'importe': '2250.00'},
      ],
      'movimientos': [
        {
          'momento': '2026-10-07T15:00:00Z', 'fecha': '2026-10-07', 'tipo': 'venta',
          'etiqueta': 'Ventas', 'folio': 'VEND01-000001', 'cliente': 'La Esquina',
          'detalle': 'De contado', 'importe': '2250.00', 'estado': 'confirmada',
          'marca': false, 'ref': 'venta-1',
        },
        {
          'momento': null, 'fecha': '2026-10-07', 'tipo': 'no_venta',
          'etiqueta': 'Visitas sin venta', 'folio': '3', 'cliente': 'Don Pepe',
          'detalle': 'Cerrado', 'importe': null, 'estado': null, 'marca': null,
          'ref': 'nd-1',
        },
      ],
      'recortado': false,
      'limite': 500,
      'telefonos': [
        {
          'etiqueta': 'Moto G54', 'estado': 'activo',
          'ultima_sync_push_en': null, 'ultima_sync_pull_en': null, 'cola_pendiente': 2,
        },
      ],
    });
    final d = await ClienteVendedores(t).detalle('v1', tipo: 'venta');
    expect(t.pedidas.single.$2, {'periodo': 'hoy', 'tipo': 'venta'});
    expect(d.resumen.single.importe.centavos, 225000);
    expect(d.movimientos.first.ref, 'venta-1');
    expect(d.movimientos.last.importe, isNull);
    expect(d.movimientos.last.marca, isFalse);
    expect(d.telefonos.single.colaPendiente, 2);
  });

  test('el camión dice qué renglón está en negativo', () async {
    final t = _Transporte(200, {
      'vendedor': 'Juan Pérez', 'camion': 'Camión 01', 'piezas': '55.000',
      'ultimo_contacto': null,
      'existencias': [
        {'producto_id': 'p1', 'sku': 'A', 'nombre': 'Atún', 'unidad_base': 'PZA',
         'cantidad': '60.000'},
        {'producto_id': 'p2', 'sku': 'B', 'nombre': 'Maruchan', 'unidad_base': 'PZA',
         'cantidad': '-5.000'},
      ],
    });
    final c = await ClienteVendedores(t).camion('v1');
    expect(c.existencias.map((e) => e.negativa), [false, true]);
  });

  test('la venta trae sus partidas', () async {
    final t = _Transporte(200, {
      'id': 'venta-1', 'folio': 'VEND01-000001', 'fecha_operativa': '2026-10-07',
      'momento': '2026-10-07T15:00:00Z', 'vendedor': 'Juan Pérez',
      'cliente': 'La Esquina', 'tipo': 'contado', 'forma_pago': 'transferencia',
      'pago_estado': 'por_confirmar', 'estado': 'confirmada', 'total': '2250.00',
      'partidas': [
        {'linea': 1, 'sku': 'A', 'nombre': 'Atún', 'unidad': 'PZA',
         'cantidad': '180.000', 'precio_unitario': '12.50', 'importe': '2250.00'},
      ],
    });
    final v = await ClienteVendedores(t).venta('venta-1');
    expect(t.pedidas.single.$1, '/v1/vendedores/ventas/venta-1');
    expect(v.partidas.single.precioUnitario.centavos, 1250);
    expect(v.formaDePago, FormaDePago.transferencia);
    expect(v.pagoEstado, 'por_confirmar');
  });

  test('un servidor sin la ruta se distingue de un vendedor que no existe', () async {
    expect(
      () => ClienteVendedores(_Transporte(404, '{"detail":"Not Found"}')).lista(),
      throwsA(isA<ServidorSinEstaFuncion>()),
    );
    expect(
      () => ClienteVendedores(_Transporte(404, '{"detail":"ese vendedor no existe"}'))
          .detalle('x'),
      throwsA(isA<ServidorConProblemas>()),
    );
    expect(
      () => ClienteCargas(_Transporte(404, '{"detail":"Not Found"}')).abiertas(),
      throwsA(isA<ServidorSinEstaFuncion>()),
    );
  });

  test('sin el permiso, se dice como permiso', () async {
    expect(
      () => ClienteVendedores(_Transporte(403, '{"detail":"falta"}')).lista(),
      throwsA(isA<SinPermisoDeVendedores>()),
    );
  });

  test('la oficina ve vendedores; el vendedor no', () {
    const gerente = Perfil(usuarioId: 'g', codigo: 'G', nombre: 'G', rol: 'gerente',
        permisos: ['ventas.ver_todas']);
    const vendedor = Perfil(usuarioId: 'v', codigo: 'V', nombre: 'V', rol: 'vendedor',
        permisos: ['ventas.crear', 'inventario.ver']);
    expect(gerente.puedeVerVendedores, isTrue);
    expect(vendedor.puedeVerVendedores, isFalse);
  });

  test('el tablero por periodo y un rango a mano', () async {
    final t = _Transporte(200, {
      'periodo': {..._periodo, 'clave': 'rango', 'desde': '2026-10-01', 'hasta': '2026-10-07'},
      'periodos': _periodos,
      'cifras': {
        'efectivo': '100.00', 'transferencias': '50.00', 'total': '150.00',
        'canceladas': 0, 'mermas': 1, 'devoluciones': 0, 'no_ventas': 2,
        'clientes_atendidos': 3, 'clientes_nuevos': 1,
      },
      'por_vendedor': [
        {'id': 'v1', 'codigo': 'VEND01', 'nombre': 'Juan', 'ventas': 2,
         'importe': '150.00', 'efectivo': '100.00', 'transferencias': '50.00',
         'mermas': 1, 'no_ventas': 2},
      ],
      'por_dia': [
        {'fecha': '2026-10-07', 'ventas': 2, 'importe': '150.00', 'efectivo': '100.00'},
      ],
    });
    final p = await ClientePeriodo(t).ver(desde: '2026-10-01', hasta: '2026-10-07');
    expect(t.pedidas.single.$1, '/v1/tablero/periodo');
    expect(t.pedidas.single.$2,
        {'periodo': 'rango', 'desde': '2026-10-01', 'hasta': '2026-10-07'});
    expect(p.cifras.total.centavos, 15000);
    expect(p.cifras.transferencias.centavos, 5000);
    expect(p.porVendedor.single.importe.centavos, 15000);
    expect(p.porDia.single.fecha, '2026-10-07');
    expect(p.periodo.enPalabras(hoy: '2026-10-07'),
        'Del jueves 1 de octubre al miércoles 7 de octubre');
  });

  test('el resumen de la empresa', () async {
    final t = _Transporte(200, {
      'clientes_activos': 120, 'prospectos': 4, 'clientes_inactivos': 2,
      'clientes_nuevos_mes': 6, 'vendedores': 5,
      'vendedores_con_camion': 4, 'usuarios_oficina': 3, 'rutas': 5, 'telefonos': 5,
      'productos': 210, 'productos_sin_precio': 1, 'bodegas': 1, 'camiones': 5,
      'piezas_en_bodegas': '4800.000', 'piezas_en_camiones': '960.000',
      'existencias_negativas': 0,
      'vendido_mes': '98000.50', 'vendido_anio': '980000.00',
    });
    final r = await ClientePeriodo(t).empresa();
    expect(t.pedidas.single.$1, '/v1/tablero/empresa');
    expect(r.clientesActivos, 120);
    expect(r.vendidoMes.centavos, 9800050);
    expect(r.piezasEnBodegas, '4800.000');
  });
}
