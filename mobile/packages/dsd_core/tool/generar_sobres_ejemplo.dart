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
    ];

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
