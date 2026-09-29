/// Genera `contracts/sobres_de_ejemplo.json`.
///
/// El archivo lo produce el cliente Dart con su propio código —el mismo
/// `SobreLocal` y el mismo hash que usará el teléfono— y lo consume
/// `server/tests/test_contrato_dart.py`, que lo empuja por el endpoint real de
/// sincronización.
///
/// Es la única prueba del proyecto que demuestra, de punta a punta, que lo que
/// arma el dispositivo es exactamente lo que el servidor acepta. Sin ella, la
/// divergencia aparecería el primer día de piloto, en un mercado, sin señal.
///
/// Los identificadores son fijos a propósito: un archivo que cambia en cada
/// regeneración no sirve para detectar cambios de verdad en el diff.
///
/// Uso:  dart run tool/generar_sobres_ejemplo.dart
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_core/dsd_core.dart';

/// UUIDv7 fijos, con la forma real que genera el dispositivo.
const _ids = [
  '019283a0-0001-7000-8000-000000000001',
  '019283a0-0002-7000-8000-000000000002',
  '019283a0-0003-7000-8000-000000000003',
  '019283a0-0004-7000-8000-000000000004',
];

List<SobreLocal> construirSobres() => [
      // Caso 1: alta simple, el pan de cada día en la ruta.
      SobreLocal(
        operacionId: '019283b0-0001-7000-8000-000000000001',
        secuencia: 0,
        visitaId: '019283c0-0001-7000-8000-000000000001',
        operaciones: [
          OperacionLocal(
            tipo: 'cliente.crear',
            entidadId: _ids[0],
            datos: {
              'nombre_comercial': 'Abarrotes Doña Mary',
              'telefono': '5512345678',
              'ubicacion_origen': 'gps',
              'lat': '19.4326000',
              'lng': '-99.1332000',
              'ubicacion_precision_m': '8.50',
            },
          ),
        ],
      ),

      // Caso 2: acentos y emoji. Si el escape difiere entre lenguajes, el hash
      // no coincide y el sobre acaba en cuarentena.
      SobreLocal(
        operacionId: '019283b0-0002-7000-8000-000000000002',
        secuencia: 1,
        visitaId: '019283c0-0002-7000-8000-000000000002',
        operaciones: [
          OperacionLocal(
            tipo: 'cliente.crear',
            entidadId: _ids[1],
            datos: {
              'nombre_comercial': 'La Esquina de Ñoño 🏪',
              'referencias': 'Frente al parque, portón café',
              'ubicacion_origen': 'manual',
              'lat': '20.6597000',
              'lng': '-103.3496000',
            },
          ),
        ],
      ),

      // Caso 3: dos altas en una sola visita. Entra completo o no entra nada.
      SobreLocal(
        operacionId: '019283b0-0003-7000-8000-000000000003',
        secuencia: 2,
        visitaId: '019283c0-0003-7000-8000-000000000003',
        operaciones: [
          OperacionLocal(
            tipo: 'cliente.crear',
            entidadId: _ids[2],
            datos: {'nombre_comercial': 'Tienda del mercado, local 12'},
          ),
          OperacionLocal(
            tipo: 'cliente.crear',
            entidadId: _ids[3],
            datos: {'nombre_comercial': 'Tienda del mercado, local 13'},
          ),
        ],
      ),

      // Caso 4: LA VISITA COMPLETA — alta del cliente y su venta en el MISMO
      // sobre. Es el escenario real del alta en la calle: el vendedor registra
      // la tienda y le vende en ese momento, y el servidor tiene que aplicar las
      // dos operaciones en orden dentro de una transacción. Si la venta se
      // aplicara antes que el cliente, no habría a quién colgársela.
      //
      // La venta trae el precio de 4 decimales que hace que la caja de 24 valga
      // 296.00 y no 295.92, y una segunda partida por pieza del mismo producto:
      // dos renglones distintos que descuentan la misma existencia.
      SobreLocal(
        operacionId: '019283b0-0004-7000-8000-000000000004',
        secuencia: 3,
        visitaId: '019283c0-0004-7000-8000-000000000004',
        operaciones: [
          OperacionLocal(
            tipo: 'cliente.crear',
            entidadId: _clienteDeLaVenta,
            datos: {
              'nombre_comercial': 'Cremería Los Compadres',
              'ubicacion_origen': 'gps',
              'lat': '19.4326000',
              'lng': '-99.1332000',
            },
          ),
          OperacionLocal(
            tipo: 'venta.crear',
            entidadId: '019283e0-0001-7000-8000-000000000001',
            datos: {
              'folio_consecutivo': 124,
              'folio_local': 'VEND01-000124',
              'cliente_id': _clienteDeLaVenta,
              'dispositivo_id': '019283f0-0001-7000-8000-000000000001',
              'tipo': 'contado',
              'lista_precios_id': _listaDePrecios,
              'lista_precios_version': 7,
              // 2 cajas a 296.0000 = 592.00, más 3 piezas a 12.3333 = 37.00.
              'subtotal': '629.00',
              'descuento': '0.00',
              'impuestos': '0.00',
              'total': '629.00',
              'lat': '19.4326000',
              'lng': '-99.1332000',
              'ubicacion_precision_m': '8.50',
              'fecha_dispositivo': '2026-09-29T17:42:03.250Z',
              'fecha_operativa': '2026-09-29',
              'partidas': [
                {
                  'id': '019283e1-0001-7000-8000-000000000001',
                  'linea': 1,
                  'producto_id': _productoDeLaVenta,
                  'unidad_codigo': 'CAJA',
                  'factor_unidad': '24.0000',
                  'cantidad': '2.000',
                  'cantidad_base': '48.000',
                  'precio_unitario': '296.0000',
                  'descuento': '0.00',
                  'importe': '592.00',
                },
                {
                  'id': '019283e1-0002-7000-8000-000000000002',
                  'linea': 2,
                  'producto_id': _productoDeLaVenta,
                  'unidad_codigo': 'PZA',
                  'factor_unidad': '1.0000',
                  'cantidad': '3.000',
                  'cantidad_base': '3.000',
                  // 3 × 12.3333 = 36.9999 → 37.00. Medio hacia arriba, igual
                  // que en Python y en el CHECK de PostgreSQL.
                  'precio_unitario': '12.3333',
                  'descuento': '0.00',
                  'importe': '37.00',
                },
              ],
            },
          ),
        ],
      ),
    ];

/// El cliente y el producto de la venta de ejemplo. El servidor los siembra con
/// estos mismos ids en `test_contrato_dart.py`.
const _clienteDeLaVenta = '019283a0-0005-7000-8000-000000000005';
const _productoDeLaVenta = '019283a0-0006-7000-8000-000000000006';
const _listaDePrecios = '019283a0-0007-7000-8000-000000000007';

void main() {
  final sobres = construirSobres();
  final documento = {
    'version': 1,
    'descripcion': 'Sobres armados por el cliente Dart. Los empuja '
        'server/tests/test_contrato_dart.py contra el endpoint real. '
        'Regenerar con: dart run tool/generar_sobres_ejemplo.dart',
    'lote_id': '019283d0-0001-7000-8000-000000000001',
    'sobres': sobres.map((s) => s.aMapa()).toList(),
  };

  final salida = File('../../../contracts/sobres_de_ejemplo.json');
  salida.writeAsStringSync(
    '${const JsonEncoder.withIndent('  ').convert(documento)}\n',
  );
  stdout.writeln('${sobres.length} sobres escritos en ${salida.path}');
  for (final s in sobres) {
    stdout.writeln('  ${s.operacionId}  ${s.hash.substring(0, 16)}…');
  }
}
