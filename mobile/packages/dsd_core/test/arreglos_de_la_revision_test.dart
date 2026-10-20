/// Lo que el teléfono lee del servidor para los arreglos de la revisión (ADR 0002
/// §157): el reloj desfasado, los cortes sin liquidación, descartar un corte, la
/// carga para hoy y el mayoreo aparte.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

class _Transporte implements Transporte {
  _Transporte(this.cuerpo);

  final Object? cuerpo;
  final List<(String, Object?)> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async {
    pedidas.add((ruta, parametros));
    return RespuestaHttp(200, jsonEncode(cuerpo));
  }

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> c) async {
    pedidas.add((ruta, c));
    return RespuestaHttp(200, jsonEncode(cuerpo));
  }
}

void main() {
  test('la respuesta del envío dice cuánto va mal el reloj, y se guarda', () {
    final r = RespuestaPush.deJson({
      'aceptadas': 0, 'duplicadas': 0, 'rechazadas': 0, 'resultados': <Object?>[],
      'desfase_reloj_seg': 3600,
    });
    expect(r.desfaseRelojSeg, 3600);
    // Un servidor anterior no lo manda.
    expect(
      RespuestaPush.deJson({
        'aceptadas': 0, 'duplicadas': 0, 'rechazadas': 0, 'resultados': <Object?>[],
      }).desfaseRelojSeg,
      isNull,
    );

    final db = sqlite3.openInMemory();
    aplicarEsquemaLocal(db);
    final aplicador = AplicadorDeltas(db);
    aplicador.guardarDesfaseDeReloj(3600);
    // Sin medida, se queda la última que sí se midió.
    aplicador.guardarDesfaseDeReloj(null);
    expect(
      db.select("SELECT valor FROM sync_estado WHERE clave = 'desfase_reloj_seg'").single['valor'],
      '3600',
    );
    db.dispose();
  });

  test('los cortes del día traen los del vendedor que no tuvieron liquidación', () async {
    final t = _Transporte({
      'por_cortar': <Object?>[],
      'cortes': <Object?>[],
      'sin_liquidacion': [
        {'id': 'k1', 'fecha_operativa': '2026-10-19', 'vendedor': 'Juan',
         'efectivo_declarado': '2250.00', 'efectivo_recibido': null, 'nota': null},
      ],
    });
    final l = await ClienteCortes(t).lista(dia: '2026-10-19');
    final k = l.sinLiquidacion.single;
    expect((k.id, k.fecha, k.vendedor, k.declarado.texto, k.recibido), (
      'k1', '2026-10-19', 'Juan', '2250.00', null,
    ));
    // Un servidor anterior no los manda.
    final viejo = ListaDeCortes.deJson({'por_cortar': <Object?>[], 'cortes': <Object?>[]});
    expect(viejo.sinLiquidacion, isEmpty);
  });

  test('descartar un corte va a su ruta y devuelve el aviso', () async {
    final t = _Transporte({'mensaje': 'Corte descartado: su carga vuelve a «Sin cortar».'});
    final aviso = await ClienteCortes(t).descartar('lq1');
    expect(t.pedidas.single.$1, '/v1/cortes/lq1/descartar');
    expect(aviso, contains('Corte descartado'));
  });

  test('la carga se abre para el día que se elige; sin día, para mañana', () async {
    final detalle = {
      'id': 'c1', 'folio': 'CG-1', 'estado': 'borrador', 'fecha_operativa': '2026-10-20',
      'vendedor': 'Juan', 'camion': 'C1', 'bodega': 'B', 'editable': true,
      'renglones': <Object?>[], 'surtido': <Object?>[], 'bloqueos': <Object?>[],
    };
    final t = _Transporte(detalle);
    await ClienteCargas(t).abrir(vendedorId: 'v1', bodegaId: 'b1', fecha: '2026-10-20');
    await ClienteCargas(t).abrir(vendedorId: 'v1', bodegaId: 'b1');
    expect(t.pedidas[0].$2, {
      'vendedor_id': 'v1', 'almacen_origen_id': 'b1', 'fecha_operativa': '2026-10-20',
    });
    expect(t.pedidas[1].$2, {'vendedor_id': 'v1', 'almacen_origen_id': 'b1'});
  });

  test('el mayoreo se lee aparte; sin él, cero', () {
    final empresa = ResumenDeLaEmpresa.deJson({
      'vendido_mes': '10500.00', 'vendido_anio': '10500.00',
      'mayoreo_mes': '500.00', 'mayoreo_anio': '500.00',
    });
    expect((empresa.mayoreoMes.texto, empresa.mayoreoAnio.texto), ('500.00', '500.00'));
    final vieja = ResumenDeLaEmpresa.deJson({'vendido_mes': '1.00', 'vendido_anio': '1.00'});
    expect(vieja.mayoreoMes, Dinero.cero);
  });
}
