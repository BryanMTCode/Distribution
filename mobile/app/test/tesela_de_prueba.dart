/// Una tesela del mapa como las de Protomaps, armada a mano (ADR 0002 §130).
///
/// 4096 × 4096 de extensión, como todas. Tierra en toda la tesela; mar en el
/// cuarto de abajo a la derecha; un parque arriba a la derecha; una calle chica
/// («Av. del Mar») de lado a lado a media altura, y una carretera de arriba a
/// abajo en el primer cuarto.
library;

import 'dart:typed_data';

import 'package:vector_tile/raw/raw_vector_tile.dart';

int _zz(int n) => (n << 1) ^ (n >> 31);

/// Los comandos de geometría del formato: «muévete», «traza», «cierra».
List<int> _trazo(List<(int, int)> puntos, {bool cerrar = false}) {
  final salida = <int>[];
  var x = 0, y = 0;
  for (var i = 0; i < puntos.length; i++) {
    if (i == 0) salida.add(1 | (1 << 3));
    if (i == 1) salida.add(2 | ((puntos.length - 1) << 3));
    final (px, py) = puntos[i];
    salida..add(_zz(px - x))..add(_zz(py - y));
    x = px;
    y = py;
  }
  if (cerrar) salida.add(7 | (1 << 3));
  return salida;
}

VectorTile_Layer _capa(String nombre, List<(VectorTile_GeomType, List<int>, Map<String, String>)> cosas) {
  final llaves = <String>[];
  final valores = <String>[];
  final rasgos = <VectorTile_Feature>[];
  for (final (tipo, geometria, propiedades) in cosas) {
    final etiquetas = <int>[];
    propiedades.forEach((k, v) {
      if (!llaves.contains(k)) llaves.add(k);
      if (!valores.contains(v)) valores.add(v);
      etiquetas..add(llaves.indexOf(k))..add(valores.indexOf(v));
    });
    rasgos.add(VectorTile_Feature(tags: etiquetas, type: tipo, geometry: geometria));
  }
  return VectorTile_Layer(
    name: nombre,
    version: 2,
    extent: 4096,
    keys: llaves,
    values: [for (final v in valores) VectorTile_Value(stringValue: v)],
    features: rasgos,
  );
}

const _poligono = VectorTile_GeomType.POLYGON;
const _linea = VectorTile_GeomType.LINESTRING;

Uint8List teselaDeMazatlan() => VectorTile(layers: [
      _capa('earth', [
        (_poligono, _trazo([(0, 0), (4096, 0), (4096, 4096), (0, 4096)], cerrar: true), {}),
      ]),
      _capa('water', [
        (_poligono, _trazo([(3072, 3072), (4096, 3072), (4096, 4096), (3072, 4096)],
            cerrar: true), {'kind': 'ocean'}),
      ]),
      _capa('landuse', [
        (_poligono, _trazo([(3072, 0), (4096, 0), (4096, 1024), (3072, 1024)], cerrar: true),
            {'kind': 'park'}),
      ]),
      _capa('roads', [
        (_linea, _trazo([(0, 2048), (4096, 2048)]), {'kind': 'minor_road', 'name': 'Av. del Mar'}),
        (_linea, _trazo([(1024, 0), (1024, 4096)]), {'kind': 'highway'}),
      ]),
    ]).writeToBuffer();
