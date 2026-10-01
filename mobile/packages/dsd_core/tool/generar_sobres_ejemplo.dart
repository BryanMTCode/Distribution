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

      // Caso 5: LA COBRANZA — una venta a crédito y, en el MISMO sobre, el cobro
      // que la abona. Es el escenario del día de cobranza cuando el cliente
      // liquida en el momento, y prueba lo que ningún otro caso prueba: que el
      // servidor aplique el FIFO **sobre una factura que acaba de crear en la
      // misma transacción**.
      //
      // Si el orden se rompiera, el cobro no encontraría a qué aplicarse y los
      // $400 quedarían como saldo a favor de un cliente que sí debía.
      SobreLocal(
        operacionId: '019283b0-0005-7000-8000-000000000005',
        secuencia: 4,
        visitaId: '019283c0-0005-7000-8000-000000000005',
        operaciones: [
          OperacionLocal(
            tipo: 'venta.crear',
            entidadId: '019283e0-0002-7000-8000-000000000002',
            datos: {
              'folio_consecutivo': 125,
              'folio_local': 'VEND01-000125',
              'cliente_id': _clienteDeLaVenta,
              'dispositivo_id': '019283f0-0001-7000-8000-000000000001',
              'tipo': 'credito',
              'lista_precios_id': _listaDePrecios,
              'lista_precios_version': 7,
              'subtotal': '592.00',
              'descuento': '0.00',
              'impuestos': '0.00',
              'total': '592.00',
              'fecha_dispositivo': '2026-09-29T18:10:00.000Z',
              'fecha_operativa': '2026-09-29',
              'partidas': [
                {
                  'id': '019283e1-0003-7000-8000-000000000003',
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
              ],
            },
          ),
          OperacionLocal(
            tipo: 'cobro.crear',
            entidadId: '019283e2-0001-7000-8000-000000000001',
            datos: {
              'folio_consecutivo': 31,
              'folio_local': 'VEND01-000031',
              'cliente_id': _clienteDeLaVenta,
              'dispositivo_id': '019283f0-0001-7000-8000-000000000001',
              'visita_id': '019283c0-0005-7000-8000-000000000005',
              // Abona parte: deja la factura en 'parcial' con $192.00. Un cobro
              // que liquidara exacto no distinguiría el caso parcial del total.
              'importe': '400.00',
              'forma_pago': 'efectivo',
              // Lo que el teléfono CREÍA que debía. Forense: el servidor no lo
              // usa para decidir nada.
              'saldo_cache_disp': '592.00',
              'lat': '19.4326000',
              'lng': '-99.1332000',
              'fecha_dispositivo': '2026-09-29T18:12:00.000Z',
              'fecha_operativa': '2026-09-29',
            },
          ),
        ],
      ),

      // Caso 6: LO QUE EXPLICA UNA DIFERENCIA — una merma del camión y un
      // no-drop, en un solo sobre.
      //
      // Ninguno de los dos mueve dinero, y por eso es fácil que una divergencia
      // de contrato pase inadvertida: no hay un total que no cuadre. Lo que se
      // rompe es más sutil y peor — una merma que cae en cuarentena por un
      // nombre de campo deja la pérdida como faltante del vendedor, y un no-drop
      // perdido borra la visita de los reportes de efectividad.
      //
      // El no-drop lleva geosello porque **el servidor lo rechaza sin él**: es el
      // único documento del sistema que lo hace, y este sobre prueba que el
      // cliente Dart siempre lo manda.
      SobreLocal(
        operacionId: '019283b0-0006-7000-8000-000000000006',
        secuencia: 5,
        visitaId: '019283c0-0006-7000-8000-000000000006',
        operaciones: [
          OperacionLocal(
            tipo: 'merma.crear',
            entidadId: '019283e3-0001-7000-8000-000000000001',
            datos: {
              'folio_consecutivo': 7,
              'folio_local': 'VEND01-000007',
              'tipo': 'merma',
              'motivo_codigo': 'ROTO',
              'dispositivo_id': '019283f0-0001-7000-8000-000000000001',
              'almacen_id': null,
              'cliente_id': null,
              'venta_origen_id': null,
              'observaciones': 'Se cayó la tarima al frenar',
              'fecha_dispositivo': '2026-09-29T18:30:00.000Z',
              'fecha_operativa': '2026-09-29',
              'lat': '19.4326000',
              'lng': '-99.1332000',
              'ubicacion_precision_m': '12.50',
              'ubicacion_origen': 'gps',
              'detalle': [
                {
                  'id': '019283e3-0002-7000-8000-000000000002',
                  'producto_id': _productoDeLaVenta,
                  // Dos cajas de 24 capturadas como 48 piezas: la conversión la
                  // hace el teléfono, y lo que viaja es SIEMPRE unidad base.
                  'cantidad_base': '48.000',
                },
              ],
            },
          ),
          OperacionLocal(
            tipo: 'no_drop.crear',
            entidadId: '019283e4-0001-7000-8000-000000000001',
            datos: {
              'folio_consecutivo': 12,
              'cliente_id': _clienteDeLaVenta,
              'vendedor_id': '019283a0-0001-7000-8000-000000000001',
              'dispositivo_id': '019283f0-0001-7000-8000-000000000001',
              'ruta_id': null,
              'visita_id': '019283c0-0006-7000-8000-000000000006',
              'motivo_codigo': 'AGOTADO_EN_CAMION',
              // Este motivo exige nota: 'no traigo lo que pidió' sin decir qué
              // pidió no sirve para nada.
              'nota': 'Pidió la presentación de 2 litros',
              'fecha_dispositivo': '2026-09-29T18:40:00.000Z',
              'fecha_operativa': '2026-09-29',
              'lat': '19.4330000',
              'lng': '-99.1340000',
              'ubicacion_precision_m': '8.00',
              'ubicacion_origen': 'gps',
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
