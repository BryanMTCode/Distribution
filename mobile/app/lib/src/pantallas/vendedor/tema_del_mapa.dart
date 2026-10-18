/// Cómo se dibuja el mapa de Mazatlán (ADR 0002 §130).
///
/// Es el esquema de capas de Protomaps (OpenStreetMap): `earth`, `water`,
/// `landuse`, `buildings`, `roads` y `places`. El tema que trae el paquete
/// tiene setenta capas —túneles, puentes, aeropuertos, iconos de cada negocio—;
/// éste tiene las que sirven para llegar a una tienda: tierra y mar, parques,
/// manzanas, calles con su nombre y colonias. Menos capas es menos trabajo para
/// un teléfono sencillo, y un mapa que se lee al sol.
///
/// Las calles se distinguen por su tipo (`kind`); una que no traiga tipo
/// conocido se dibuja como calle chica, para que un cambio en el esquema no
/// deje huecos.
library;

import 'package:vector_tile_renderer/vector_tile_renderer.dart';

/// El nombre de la fuente en el tema; es el que se le da al proveedor.
const fuenteDelMapa = 'protomaps';

const _ancho = ['interpolate', ['exponential', 1.6], ['zoom']];

Map<String, Object> _calle(String id, List<Object> filtro, String color,
        List<Object> anchos, {List<double>? punteada}) =>
    {
      'id': id,
      'type': 'line',
      'source': fuenteDelMapa,
      'source-layer': 'roads',
      'filter': filtro,
      'layout': {'line-cap': 'round', 'line-join': 'round'},
      'paint': {
        'line-color': color,
        'line-width': [..._ancho, ...anchos],
        'line-dasharray': ?punteada,
      },
    };

Map<String, Object> _nombre(String id, String capa, List<Object> filtro,
        {required double minzoom, double tamano = 12, bool enLinea = false}) =>
    {
      'id': id,
      'type': 'symbol',
      'source': fuenteDelMapa,
      'source-layer': capa,
      'minzoom': minzoom,
      'filter': filtro,
      'layout': {
        'text-field': '{name}',
        'text-size': tamano,
        if (enLinea) 'symbol-placement': 'line',
        'text-max-width': 8,
      },
      'paint': {
        'text-color': '#3d3d3d',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.5,
      },
    };

const _chicas = ['minor_road', 'other'];
const _grandes = ['major_road', 'medium_road'];

final _capas = <Map<String, Object>>[
  {
    'id': 'mar',
    'type': 'background',
    'paint': {'background-color': '#aad3df'},
  },
  {
    'id': 'tierra',
    'type': 'fill',
    'source': fuenteDelMapa,
    'source-layer': 'earth',
    'paint': {'fill-color': '#f4f1ea'},
  },
  {
    'id': 'parques',
    'type': 'fill',
    'source': fuenteDelMapa,
    'source-layer': 'landuse',
    'filter': [
      'in', 'kind', 'park', 'cemetery', 'golf_course', 'forest', 'nature_reserve',
      'protected_area', 'village_green', 'playground', 'grass', 'wood',
    ],
    'paint': {'fill-color': '#d3e9c8'},
  },
  {
    'id': 'playa',
    'type': 'fill',
    'source': fuenteDelMapa,
    'source-layer': 'landuse',
    'filter': ['in', 'kind', 'beach', 'sand'],
    'paint': {'fill-color': '#f5e9c4'},
  },
  {
    'id': 'agua',
    'type': 'fill',
    'source': fuenteDelMapa,
    'source-layer': 'water',
    'filter': ['==', r'$type', 'Polygon'],
    'paint': {'fill-color': '#aad3df'},
  },
  {
    'id': 'manzanas',
    'type': 'fill',
    'source': fuenteDelMapa,
    'source-layer': 'buildings',
    'minzoom': 15,
    'paint': {'fill-color': '#e2dbd0', 'fill-opacity': 0.8},
  },
  // Los bordes primero, debajo de todas: así una calle que cruza otra no la
  // corta con su borde.
  _calle('borde_chicas', ['!in', 'kind', 'highway', 'major_road', 'medium_road', 'path'],
      '#d6d0c6', [12, 0.5, 14, 2.5, 16, 6, 18, 17]),
  _calle('borde_grandes', ['in', 'kind', ..._grandes], '#d9b56b',
      [8, 0.8, 12, 2.4, 15, 5, 18, 18]),
  _calle('borde_carretera', ['==', 'kind', 'highway'], '#c98a3a',
      [5, 1, 12, 3, 15, 7, 18, 20]),
  _calle('veredas', ['==', 'kind', 'path'], '#c9c2b6', [14, 0.6, 18, 2.5],
      punteada: [2, 1.5]),
  _calle('chicas', ['!in', 'kind', 'highway', 'major_road', 'medium_road', 'path'],
      '#ffffff', [12, 0.3, 14, 1.6, 16, 4.5, 18, 14]),
  _calle('grandes', ['in', 'kind', ..._grandes], '#fde9a8',
      [8, 0.5, 12, 1.6, 15, 3.8, 18, 15]),
  _calle('carretera', ['==', 'kind', 'highway'], '#f9c66b',
      [5, 0.6, 12, 2, 15, 5.5, 18, 17]),
  _nombre('nombre_carretera', 'roads', ['in', 'kind', 'highway', ..._grandes],
      minzoom: 13, enLinea: true),
  _nombre('nombre_calle', 'roads', ['in', 'kind', ..._chicas], minzoom: 15,
      tamano: 11, enLinea: true),
  _nombre('colonias', 'places', ['in', 'kind', 'neighbourhood', 'suburb', 'quarter'],
      minzoom: 13, tamano: 12),
  _nombre('ciudades', 'places', ['in', 'kind', 'locality', 'city', 'town', 'village'],
      minzoom: 8, tamano: 15),
];

/// El tema, listo para `VectorTileLayer`.
Theme temaDelMapa() => ThemeReader().read({
      'version': 8,
      'sources': {
        fuenteDelMapa: {'type': 'vector'},
      },
      'layers': _capas,
    });
