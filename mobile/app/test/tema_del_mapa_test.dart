/// El tema del mapa dibuja lo que trae Protomaps (ADR 0002 §130).
///
/// · El tema se lee: una sola fuente, `protomaps`, con sus capas.
/// · Una tesela armada a mano con el esquema de Protomaps sale con sus colores:
///   tierra, mar, parque, la calle chica en blanco y la carretera en naranja.
/// · El archivo que arma el servidor (`mapa.extraer`, en `test/fijos`) se abre
///   con el proveedor de la app, la tesela sale descomprimida y se dibuja igual:
///   la cadena entera, de Python a la pantalla.
library;

import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:dsd_app/src/pantallas/vendedor/tema_del_mapa.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vector_map_tiles/vector_map_tiles.dart';
import 'package:vector_map_tiles_pmtiles/vector_map_tiles_pmtiles.dart';
import 'package:vector_tile_renderer/vector_tile_renderer.dart' hide TileLayer;

import 'tesela_de_prueba.dart';

Future<ByteData> _dibujar(Uint8List bytes, {double zoom = 14.9}) async {
  final tema = temaDelMapa();
  final tesela = TileFactory(tema, const Logger.noop())
      .create(VectorTileReader().read(bytes));
  final imagen = await ImageRenderer(theme: tema, scale: 1).render(
    TileSource(tileset: Tileset({fuenteDelMapa: tesela})),
    zoom: zoom,
  );
  expect(imagen.width, 256);
  return (await imagen.toByteData(format: ui.ImageByteFormat.rawRgba))!;
}

Color _pixel(ByteData datos, int x, int y) {
  final i = (y * 256 + x) * 4;
  return Color.fromARGB(
    datos.getUint8(i + 3), datos.getUint8(i), datos.getUint8(i + 1), datos.getUint8(i + 2),
  );
}

Matcher _casi(int argb) => predicate<Color>((c) {
      final e = Color(argb);
      return (c.r - e.r).abs() < 0.05 && (c.g - e.g).abs() < 0.05 && (c.b - e.b).abs() < 0.05;
    }, 'cerca de 0x${argb.toRadixString(16)}');

void _colores(ByteData d) {
  expect(_pixel(d, 100, 60), _casi(0xFFF4F1EA), reason: 'tierra');
  expect(_pixel(d, 240, 240), _casi(0xFFAAD3DF), reason: 'mar');
  expect(_pixel(d, 240, 20), _casi(0xFFD3E9C8), reason: 'parque');
  expect(_pixel(d, 180, 128), _casi(0xFFFFFFFF), reason: 'calle chica');
  expect(_pixel(d, 64, 60), _casi(0xFFF9C66B), reason: 'carretera');
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('el tema se lee: una fuente y sus capas', () {
    final tema = temaDelMapa();
    expect(tema.tileSources, {fuenteDelMapa});
    expect(tema.layers.map((c) => c.id), containsAll(
        ['mar', 'tierra', 'agua', 'parques', 'chicas', 'carretera', 'nombre_calle', 'colonias']));
  });

  test('una tesela de Protomaps sale con sus colores', () async {
    _colores(await _dibujar(teselaDeMazatlan()));
  });

  test('el archivo que arma el servidor se abre y se dibuja', () async {
    final proveedor =
        await PmTilesVectorTileProvider.fromSource('test/fijos/mazatlan_prueba.pmtiles');
    expect(proveedor.maximumZoom, 15);
    // La tesela de zoom 15 del centro de la zona.
    final bytes = await proveedor.provide(TileIdentity(15, 6698, 14208));
    expect(bytes, teselaDeMazatlan());
    _colores(await _dibujar(bytes));
    // Lo de fuera de la zona no viene.
    await expectLater(
      proveedor.provide(TileIdentity(15, 6600, 14208)),
      throwsA(isA<ProviderException>()),
    );
  });
}
