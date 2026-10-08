/// Los clientes de `/v1/cortes` y `/v1/oficina/clientes`.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.codigo, this.cuerpo);

  final int codigo;
  final Object? cuerpo;
  final List<(String, Object?)> pedidas = [];

  RespuestaHttp get _r =>
      RespuestaHttp(codigo, cuerpo is String ? cuerpo! as String : jsonEncode(cuerpo));

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    return _r;
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    pedidas.add((ruta, cuerpo));
    return _r;
  }
}

final _corte = {
  'id': 'lq1', 'folio': 'LQ-000001', 'estado': 'abierta', 'abierto': true,
  'fecha_operativa': '2026-10-07', 'vendedor': 'Juan', 'camion': 'Camión 01',
  'carga': 'CG-000001', 'efectivo_esperado': '2250.00', 'efectivo_entregado': '0.00',
  'diferencia_efectivo': '-2250.00', 'arqueo_hecho': false, 'observaciones': null,
  'renglones': [
    {'id': 'r1', 'sku': 'A', 'nombre': 'Atún', 'unidad_base': 'PZA', 'inicial': '0.000',
     'cargada': '240.000', 'vendida': '180.000', 'merma': '0.000', 'devuelta': '0.000',
     'esperado': '60.000', 'contada': '55.000', 'diferencia': '-5.000',
     'presentaciones': <Object?>[]},
  ],
  'bloqueos': ['«Moto» reportó 2 operación(es) sin subir.'],
  'respaldo': {'respaldado': false, 'motivo': 'nunca reportó', 'pendientes': 2},
  'cargos': <Object?>[], 'total_cargado': '0.00', 'mensaje': 'Conteo guardado.',
};

void main() {
  group('cortes', () {
    test('lee el corte y dice si cada renglón cuadra', () async {
      final c = await ClienteCortes(_Transporte(200, _corte)).ver('lq1');
      expect(c.renglones.single.cuadra, isFalse);
      expect(c.efectivoEsperado.centavos, 225000);
      expect(c.respaldado, isFalse);
      expect(c.bloqueos, hasLength(1));
    });

    test('el conteo y el cierre viajan como los pide el servidor', () async {
      final t = _Transporte(200, _corte);
      await ClienteCortes(t).contar('lq1', {'r1': '55'});
      await ClienteCortes(t).cerrar('lq1', confirmoSincronizado: true);
      expect(t.pedidas[0].$1, '/v1/cortes/lq1/conteo');
      expect(t.pedidas[0].$2, {'contados': {'r1': '55'}});
      expect(t.pedidas[1].$1, '/v1/cortes/lq1/cerrar');
      expect(t.pedidas[1].$2, {'confirmo_sincronizado': true});
    });

    test('un 409 trae el texto del servidor; un 403 es permiso', () async {
      expect(
        () => ClienteCortes(_Transporte(409, {'detail': 'No se puede cerrar: x'})).cerrar('lq1'),
        throwsA(isA<CargaRechazada>().having((e) => e.detalle, 'detalle', 'No se puede cerrar: x')),
      );
      expect(
        () => ClienteCortes(_Transporte(403, {'detail': 'falta'})).lista(),
        throwsA(isA<SinPermisoDeCortar>()),
      );
    });
  });

  group('clientes de la oficina', () {
    test('la lista trae los conteos de cada filtro', () async {
      final t = _Transporte(200, {
        'filtro': 'sin_ubicacion', 'recortado': false,
        'conteos': {'todos': 2, 'prospectos': 0, 'sin_ubicacion': 1},
        'clientes': [
          {'id': 'c1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'ruta': 'R4',
           'estatus': 'activo', 'telefono': null, 'con_ubicacion': false,
           'ultima_compra': null},
        ],
      });
      final l = await ClienteClientesDeOficina(t).lista(filtro: 'sin_ubicacion', q: ' esq ');
      expect(t.pedidas.single.$2, {'filtro': 'sin_ubicacion', 'q': 'esq'});
      expect(l.conteos['sin_ubicacion'], 1);
      expect(l.clientes.single.conUbicacion, isFalse);
    });

    test('la ficha trae la ubicación y cómo pagó cada venta', () async {
      final t = _Transporte(200, {
        'id': 'c1', 'codigo': 'CLI-1', 'nombre': 'La Esquina', 'razon_social': null,
        'contacto': null, 'telefono': null, 'direccion': null, 'referencias': null,
        'ruta': 'R4', 'estatus': 'activo', 'lat': '23.2329000', 'lng': '-106.4062000',
        'ubicacion_origen': 'gps', 'ubicacion_capturada_en': null,
        'comprado_mes': '250.00', 'comprado_anio': '250.00',
        'ventas': [
          {'id': 'v1', 'folio': 'V-1', 'fecha': '2026-10-08', 'momento': null,
           'tipo': 'contado', 'forma_pago': 'transferencia', 'estado': 'confirmada',
           'total': '250.00', 'vendedor': 'Juan'},
          {'id': 'v0', 'folio': 'V-0', 'fecha': '2026-09-20', 'momento': null,
           'tipo': 'credito', 'forma_pago': null, 'estado': 'confirmada',
           'total': '90.00', 'vendedor': 'Juan'},
        ],
      });
      final f = await ClienteClientesDeOficina(t).ficha('c1');
      expect(f.lat, closeTo(23.2329, 1e-7));
      expect(f.conUbicacion, isTrue);
      expect(f.ventas.first.formaDePago, FormaDePago.transferencia);
      expect(f.ventas.last.formaDePago, isNull);
    });
  });

  test('los permisos del perfil', () {
    const gerente = Perfil(usuarioId: 'g', codigo: 'G', nombre: 'G', rol: 'gerente',
        permisos: ['inventario.liquidar']);
    const vendedor = Perfil(usuarioId: 'v', codigo: 'V', nombre: 'V', rol: 'vendedor',
        permisos: ['ventas.crear']);
    expect(gerente.puedeCortar, isTrue);
    expect(vendedor.puedeCortar, isFalse);
  });
}
