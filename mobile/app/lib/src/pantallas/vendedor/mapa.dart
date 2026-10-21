/// El mapa dentro de la app, sin señal (ADR 0002 §130).
///
/// «Yo quiero que les aparezca el mapa en la app.» Las calles salen del mapa de
/// Mazatlán que el teléfono bajó una vez del servidor (`estado/mapa.dart`); las
/// tiendas, de la base local; el punto azul, del GPS. Ninguna de las tres cosas
/// pide datos, así que en una colonia sin cobertura el mapa se ve igual que en
/// la oficina.
///
/// Sin el mapa descargado la pantalla sirve igual: las tiendas y el vendedor
/// sobre un fondo liso, que ya dice qué queda cerca de qué, y arriba cómo
/// bajarlo.
///
/// Las tiendas van de color: las de hoy por visitar en rojo, las reagendadas
/// para hoy en morado (§168), las ya visitadas en verde con su palomita, y las
/// demás de la ruta en gris. Tocar una abre su ficha con
/// «Venderle» —la visita ahí mismo, sin regresar a buscarla en la lista (§166)—,
/// «Guíame» (la flecha de §129), «Abrir en Mapas» y su perfil. Entrando desde
/// la guía, la tienda va marcada y una línea punteada la une con el vendedor.
library;

import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:latlong2/latlong.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:vector_map_tiles/vector_map_tiles.dart';
import 'package:vector_map_tiles_pmtiles/vector_map_tiles_pmtiles.dart';

import '../../datos/mapa_offline.dart';
import '../../datos/repo_clientes.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart';
import '../../estado/mapa.dart';
import '../../estado/sesion.dart';
import '../../marca.dart';
import 'catalogo.dart';
import 'guia.dart';
import 'perfil_cliente.dart';
import 'tema_del_mapa.dart';

/// El centro de Mazatlán, para cuando no hay ni tiendas ni GPS.
const _mazatlan = LatLng(23.2329, -106.4062);

/// La capa de las calles, con el archivo del teléfono. Nula si el archivo no
/// abre (se dañó): la pantalla lo dice y ofrece volver a bajarlo.
///
/// Va por versión, no solo por archivo: al bajar uno nuevo el archivo se llama
/// igual, y sin la versión la pantalla seguiría con el viejo abierto. Por lo
/// mismo, las teselas ya dibujadas se guardan en una carpeta de esa versión, y
/// las de versiones anteriores se borran.
final capaDeCallesProvider =
    FutureProvider.family<Widget?, ({String ruta, String version})>((ref, mapa) async {
  try {
    final proveedor = await PmTilesVectorTileProvider.fromSource(mapa.ruta);
    final cache = await _cacheDeLaVersion(mapa.version);
    return VectorTileLayer(
      key: const Key('capa_calles'),
      tileProviders: TileProviders({fuenteDelMapa: proveedor}),
      theme: _tema,
      // En imágenes: cada tesela se dibuja una vez y se guarda, que es lo que
      // aguanta un teléfono sencillo al mover el mapa.
      layerMode: VectorTileLayerMode.raster,
      maximumZoom: 18,
      concurrency: 2,
      fileCacheMaximumSizeInBytes: 40 * 1024 * 1024,
      cacheFolder: () async => cache,
    );
  } on Object {
    return null;
  }
});

Future<Directory> _cacheDeLaVersion(String version) async {
  final base = Directory(p.join((await getTemporaryDirectory()).path, 'mapa_dibujado'));
  final suya = Directory(p.join(base.path, version.isEmpty ? 'sin_version' : version));
  try {
    if (base.existsSync()) {
      for (final otra in base.listSync().whereType<Directory>()) {
        if (otra.path != suya.path) otra.deleteSync(recursive: true);
      }
    }
  } on FileSystemException {
    // Es basura de un mapa anterior: si no se puede borrar hoy, será otro día.
  }
  return suya;
}

final _tema = temaDelMapa();

class PantallaMapa extends ConsumerStatefulWidget {
  const PantallaMapa({super.key, this.destino});

  /// La tienda a la que se va, si se entró desde su guía.
  final ClienteEnRuta? destino;

  @override
  ConsumerState<PantallaMapa> createState() => _EstadoMapa();
}

class _EstadoMapa extends ConsumerState<PantallaMapa> {
  final _control = MapController();
  StreamSubscription<LecturaGps>? _lecturas;
  LecturaGps? _ultima;
  bool _listo = false;
  bool _juntosEnPantalla = false;

  Ubicacion? get _yo => switch (_ultima) {
        GpsObtenido(:final ubicacion) => ubicacion,
        _ => null,
      };

  @override
  void initState() {
    super.initState();
    _lecturas = ref.read(servicioUbicacionProvider).seguir().listen((lectura) {
      if (!mounted) return;
      setState(() => _ultima = lectura);
      _juntarConElDestino();
    });
  }

  @override
  void dispose() {
    _lecturas?.cancel();
    super.dispose();
  }

  /// La primera vez que se sabe dónde está el vendedor, entrando desde la guía:
  /// él y la tienda en la misma pantalla.
  void _juntarConElDestino() {
    final yo = _yo;
    final d = widget.destino;
    if (!_listo || _juntosEnPantalla || yo == null || d == null) return;
    _juntosEnPantalla = true;
    _control.fitCamera(CameraFit.coordinates(
      coordinates: [LatLng(yo.lat, yo.lng), LatLng(d.lat!, d.lng!)],
      padding: const EdgeInsets.fromLTRB(48, 120, 48, 120),
      maxZoom: 17,
    ));
  }

  void _centrarEnMi() {
    final yo = _yo;
    if (yo == null) {
      final mensaje = switch (_ultima) {
        GpsApagado() => 'La ubicación del teléfono está apagada: préndela.',
        GpsSinPermiso() => 'La app no tiene permiso de ubicación.',
        _ => 'Buscando tu ubicación… afuera y sin techo es más rápido.',
      };
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(mensaje)));
      return;
    }
    _control.move(LatLng(yo.lat, yo.lng), math.max(_control.camera.zoom, 16));
  }

  @override
  Widget build(BuildContext context) {
    final destino = widget.destino;
    // Todas las de la ruta, sin el buscador de la lista ni su tope.
    final tiendas = ref
        .watch(repoClientesProvider)
        .deLaRuta(limite: 5000, hoy: ref.watch(relojProvider)())
        .where((c) => c.conUbicacion)
        .toList();
    final guardado = ref.watch(mapaGuardadoProvider).valueOrNull;
    final capa = guardado == null
        ? null
        : ref.watch(
            capaDeCallesProvider((ruta: guardado.ruta, version: guardado.version)),
          );
    final descarga = ref.watch(descargaDelMapaProvider);
    final yo = _yo;
    final colores = Theme.of(context).colorScheme;

    return Scaffold(
      key: const Key('pantalla_mapa'),
      appBar: AppBar(
        title: Text(destino?.nombreComercial ?? 'Mapa'),
        actions: [
          if (guardado != null)
            PopupMenuButton<String>(
              key: const Key('mapa_menu'),
              tooltip: 'Más',
              onSelected: (_) => ref.read(descargaDelMapaProvider.notifier).descargar(),
              itemBuilder: (_) => [
                PopupMenuItem(
                  value: 'actualizar',
                  key: const Key('mapa_actualizar'),
                  child: ListTile(
                    leading: const Icon(Icons.download_outlined),
                    title: const Text('Volver a bajar el mapa'),
                    subtitle: Text('El de ${_fecha(guardado.version)} · '
                        '${megas(guardado.bytes)} · con WiFi'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ],
            ),
        ],
      ),
      body: Stack(
        children: [
          FlutterMap(
            mapController: _control,
            options: MapOptions(
              initialCameraFit: _encuadreInicial(tiendas),
              initialCenter: _mazatlan,
              initialZoom: 13,
              minZoom: 9,
              maxZoom: 18,
              backgroundColor: const Color(0xFFF4F1EA),
              // Sin girar: con dos dedos en un camión que brinca, el mapa
              // acababa de lado y el norte ya no estaba arriba.
              interactionOptions: const InteractionOptions(
                flags: InteractiveFlag.all & ~InteractiveFlag.rotate,
              ),
              onMapReady: () {
                _listo = true;
                _juntarConElDestino();
              },
            ),
            children: [
              if (capa?.valueOrNull case final Widget calles) calles,
              if (destino != null && yo != null)
                PolylineLayer(
                  key: const Key('mapa_linea_al_destino'),
                  polylines: [
                    Polyline(
                      points: [LatLng(yo.lat, yo.lng), LatLng(destino.lat!, destino.lng!)],
                      color: colores.primary,
                      strokeWidth: 4,
                      pattern: StrokePattern.dashed(segments: const [10, 8]),
                    ),
                  ],
                ),
              MarkerLayer(
                // Las de hoy encima de las demás, y la que se busca encima de todas.
                markers: [
                  for (final c in [...tiendas]..sort(_porImportancia(destino)))
                    Marker(
                      key: Key('pin_${c.id}'),
                      point: LatLng(c.lat!, c.lng!),
                      width: c.id == destino?.id ? 48 : 36,
                      height: c.id == destino?.id ? 48 : 36,
                      alignment: Alignment.topCenter,
                      child: _Pin(
                        cliente: c,
                        esDestino: c.id == destino?.id,
                        alTocar: () => _ficha(c),
                      ),
                    ),
                ],
              ),
              if (yo != null)
                MarkerLayer(
                  markers: [
                    Marker(
                      key: const Key('mapa_yo'),
                      point: LatLng(yo.lat, yo.lng),
                      width: 24,
                      height: 24,
                      child: const _PuntoAzul(),
                    ),
                  ],
                ),
            ],
          ),
          if (guardado == null || descarga is! DescargaInactiva)
            Positioned(
              top: 8,
              left: 8,
              right: 8,
              child: AvisoDelMapa(estado: descarga, hayMapa: guardado != null),
            )
          else if (capa?.valueOrNull == null && capa?.isLoading == false)
            Positioned(
              top: 8,
              left: 8,
              right: 8,
              child: AvisoDelMapa(estado: descarga, danado: true),
            ),
          Positioned(
            left: 8,
            bottom: 8,
            right: 80,
            child: _Leyenda(conCalles: capa?.valueOrNull != null),
          ),
          Positioned(
            right: 16,
            bottom: 16,
            child: FloatingActionButton(
              key: const Key('mapa_centrar_en_mi'),
              tooltip: 'Dónde estoy',
              onPressed: _centrarEnMi,
              child: Icon(yo == null ? Icons.location_searching : Icons.my_location),
            ),
          ),
        ],
      ),
    );
  }

  CameraFit? _encuadreInicial(List<ClienteEnRuta> tiendas) {
    final d = widget.destino;
    if (d != null) {
      return CameraFit.coordinates(
        coordinates: [LatLng(d.lat!, d.lng!)],
        maxZoom: 16,
      );
    }
    final deHoy = tiendas.where((c) => c.tocaHoy).toList();
    final cuales = deHoy.isNotEmpty ? deHoy : tiendas;
    if (cuales.isEmpty) return null;
    return CameraFit.coordinates(
      coordinates: [for (final c in cuales) LatLng(c.lat!, c.lng!)],
      padding: const EdgeInsets.fromLTRB(40, 100, 40, 90),
      maxZoom: 16,
    );
  }

  Future<void> _ficha(ClienteEnRuta c) async {
    final yo = _yo;
    final distancia =
        yo?.distanciaA(Ubicacion(lat: c.lat!, lng: c.lng!, origen: OrigenUbicacion.gps));
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (hoja) => _FichaEnElMapa(cliente: c, distanciaMetros: distancia),
    );
  }
}

/// La que se busca al último (encima), luego las de hoy, luego las demás.
int Function(ClienteEnRuta, ClienteEnRuta) _porImportancia(ClienteEnRuta? destino) {
  int peso(ClienteEnRuta c) =>
      c.id == destino?.id ? 3 : (c.tocaHoy && !c.visitadoHoy ? 2 : (c.tocaHoy ? 1 : 0));
  return (a, b) => peso(a).compareTo(peso(b));
}

String _fecha(String version) {
  // «20261017» → «17/10/2026»; otra cosa, tal cual.
  if (version.length != 8 || int.tryParse(version) == null) return version;
  return '${version.substring(6)}/${version.substring(4, 6)}/${version.substring(0, 4)}';
}

class _Pin extends StatelessWidget {
  const _Pin({required this.cliente, required this.esDestino, required this.alTocar});

  final ClienteEnRuta cliente;
  final bool esDestino;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final (color, icono) = esDestino
        ? (colores.error, Icons.location_on)
        : cliente.visitadoHoy
            ? (Colors.green.shade700, Icons.where_to_vote)
            // La reagendada para hoy, aparte de las de su plan (ADR 0002 §168).
            : cliente.reprogramadaHoy
                ? (colorDeReagendada, Icons.location_on)
                : cliente.tocaHoy
                    ? (colores.primary, Icons.location_on)
                    : (Colors.blueGrey.shade400, Icons.location_on);
    return Semantics(
      button: true,
      label: cliente.nombreComercial,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: alTocar,
        child: FittedBox(
          child: Icon(
            icono,
            color: color,
            shadows: const [Shadow(color: Colors.white, blurRadius: 3)],
          ),
        ),
      ),
    );
  }
}

class _PuntoAzul extends StatelessWidget {
  const _PuntoAzul();

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(
          color: const Color(0xFF1A73E8),
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 3),
          boxShadow: const [BoxShadow(color: Colors.black26, blurRadius: 4)],
        ),
      );
}

class _Leyenda extends StatelessWidget {
  const _Leyenda({required this.conCalles});

  final bool conCalles;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final estilo = Theme.of(context).textTheme.labelSmall;
    Widget punto(Color c, String texto) => Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.circle, size: 10, color: c),
            const SizedBox(width: 3),
            Text(texto, style: estilo),
          ],
        );
    return Align(
      alignment: Alignment.bottomLeft,
      child: Card(
        key: const Key('mapa_leyenda'),
        margin: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 10,
                runSpacing: 2,
                children: [
                  punto(colores.primary, 'Hoy'),
                  punto(colorDeReagendada, 'Reagendada'),
                  punto(Colors.green.shade700, 'Visitada'),
                  punto(Colors.blueGrey.shade400, 'Otro día'),
                  punto(const Color(0xFF1A73E8), 'Tú'),
                ],
              ),
              // La licencia de OpenStreetMap (ODbL) pide decir de dónde son
              // los datos de las calles.
              if (conCalles)
                Text('Calles © OpenStreetMap', key: const Key('mapa_atribucion'),
                    style: estilo?.copyWith(color: colores.onSurfaceVariant)),
            ],
          ),
        ),
      ),
    );
  }
}

class AvisoDelMapa extends ConsumerWidget {
  const AvisoDelMapa({
    super.key,
    required this.estado,
    this.hayMapa = false,
    this.danado = false,
  });

  final EstadoDescargaDelMapa estado;
  final bool hayMapa;
  final bool danado;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final estilo = Theme.of(context).textTheme;
    final descargar = ref.read(descargaDelMapaProvider.notifier).descargar;
    final Widget cuerpo = switch (estado) {
      DescargaPreguntando() => const Row(
          children: [
            SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
            SizedBox(width: 10),
            Expanded(child: Text('Preguntando al servidor por el mapa…')),
          ],
        ),
      DescargaEnCurso(:final recibidos, :final total, :final fraccion) => Column(
          key: const Key('mapa_bajando'),
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Bajando el mapa de Mazatlán… ${megas(recibidos)}'
                '${total == null ? '' : ' de ${megas(total)}'}'),
            const SizedBox(height: 6),
            LinearProgressIndicator(value: fraccion),
            const SizedBox(height: 4),
            Text('No salgas de la app ni del WiFi hasta que termine.',
                style: estilo.bodySmall),
          ],
        ),
      DescargaLista() => Row(
          key: const Key('mapa_listo'),
          children: [
            const Icon(Icons.check_circle, color: Colors.green),
            const SizedBox(width: 8),
            const Expanded(
              child: Text('Listo: el mapa ya está en el teléfono y funciona sin señal.'),
            ),
            IconButton(
              tooltip: 'Cerrar',
              icon: const Icon(Icons.close),
              onPressed: ref.read(descargaDelMapaProvider.notifier).cerrarAviso,
            ),
          ],
        ),
      DescargaFallida(:final mensaje) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(mensaje, key: const Key('mapa_error')),
            const SizedBox(height: 6),
            Wrap(
              alignment: WrapAlignment.end,
              spacing: 8,
              children: [
                // Con un mapa ya guardado, el error de una actualización no tiene
                // por qué quedarse tapando el mapa.
                if (hayMapa)
                  TextButton(
                    key: const Key('mapa_cerrar_error'),
                    onPressed: ref.read(descargaDelMapaProvider.notifier).cerrarAviso,
                    child: const Text('Cerrar'),
                  ),
                FilledButton.tonal(
                  key: const Key('mapa_reintentar'),
                  onPressed: descargar,
                  child: const Text('Volver a intentar'),
                ),
              ],
            ),
          ],
        ),
      DescargaInactiva() => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              danado
                  ? 'El mapa guardado no abre. Vuelve a bajarlo, con WiFi.'
                  : 'Todavía no tienes el mapa de las calles. Bájalo una vez con '
                      'WiFi y después funciona sin señal.',
            ),
            const SizedBox(height: 6),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.icon(
                key: const Key('mapa_descargar'),
                onPressed: descargar,
                icon: const Icon(Icons.download),
                label: const Text('Bajar el mapa de Mazatlán'),
              ),
            ),
          ],
        ),
    };
    return Card(
      key: const Key('mapa_aviso'),
      child: Padding(padding: const EdgeInsets.all(12), child: cuerpo),
    );
  }
}

class _FichaEnElMapa extends ConsumerWidget {
  const _FichaEnElMapa({required this.cliente, this.distanciaMetros});

  final ClienteEnRuta cliente;
  final double? distanciaMetros;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final estilo = Theme.of(context).textTheme;
    final c = cliente;
    final datos = [
      if (distanciaMetros != null) 'a ${distanciaEnPalabras(distanciaMetros!)}',
      if (c.codigo != null) c.codigo,
      if (c.visitadoHoy)
        'ya visitada hoy'
      else if (c.reprogramadaHoy)
        'reagendada para hoy'
      else if (c.tocaHoy)
        'le toca hoy',
    ].whereType<String>().join(' · ');
    return SafeArea(
      child: Padding(
        key: Key('ficha_mapa_${c.id}'),
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(c.nombreComercial, style: estilo.titleLarge),
            if (datos.isNotEmpty) Text(datos, style: estilo.bodyMedium),
            if (c.direccion != null) Text(c.direccion!, style: estilo.bodySmall),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                // «Que desde el mapa al dar clic a una tienda me dé la opción de
                // venderle, para no tener que regresarme a buscarla» (§166).
                FilledButton.icon(
                  key: const Key('mapa_venderle'),
                  onPressed: () => abrirLaVisita(Navigator.of(context)..pop(), ref, c.id),
                  icon: const Icon(Icons.shopping_cart_outlined),
                  label: const Text('Venderle'),
                ),
                OutlinedButton.icon(
                  key: const Key('mapa_guiame'),
                  onPressed: () {
                    Navigator.of(context)
                      ..pop()
                      ..push(MaterialPageRoute<void>(
                        builder: (_) => PantallaGuia(cliente: c),
                      ));
                  },
                  icon: const Icon(Icons.near_me_outlined),
                  label: const Text('Guíame'),
                ),
                OutlinedButton.icon(
                  key: const Key('mapa_abrir_en_mapas'),
                  onPressed: () => abrirEnMapas(context, ref, c),
                  icon: const Icon(Icons.map_outlined),
                  label: const Text('Ruta por calles'),
                ),
                TextButton(
                  key: const Key('mapa_perfil'),
                  onPressed: () {
                    Navigator.of(context)
                      ..pop()
                      ..push(MaterialPageRoute<void>(
                        builder: (_) => PantallaPerfilDelCliente(cliente: c),
                      ));
                  },
                  child: const Text('Perfil'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
