/// El mapa de rutas de la oficina: un vendedor y un día (ADR 0002 §168).
///
/// «Quiero que en la app del gerente haya un mapa que me permita elegir el
/// conductor y día para ver la ruta.»
///
/// · Arriba, el vendedor y el día —‹ Hoy, miércoles 21 de octubre › y el
///   calendario, como las cargas y los cortes—, hacia atrás o hacia adelante.
/// · En el mapa, las tiendas que le tocan ese día en el orden de su hoja, unidas
///   por una línea punteada en ese orden; las reagendadas para ese día, y las que
///   visitó fuera de su día (§167). Cada una del color de cómo le fue: vendida,
///   visitada sin venta, por visitar (o «no se visitó», si el día ya pasó).
/// · Las calles son las del mapa de Mazatlán que el teléfono baja una vez, igual
///   que en el mapa del vendedor; sin él se ven las tiendas sobre un fondo liso.
/// · Las tiendas sin ubicación no se pueden dibujar: se cuentan arriba.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:latlong2/latlong.dart';

import '../../estado/mapa.dart';
import '../../estado/sesion.dart';
import '../../estado/vendedores.dart';
import '../../marca.dart';
import '../vendedor/mapa.dart' show AvisoDelMapa, capaDeCallesProvider;
import 'selector_de_dia.dart';

const _mazatlan = LatLng(23.2329, -106.4062);

/// La visitada sin venta: gris como en «Su ruta» (§153), más oscuro para que se
/// vea sobre las calles.
final _colorSinVenta = Colors.blueGrey.shade600;

/// La que visitó aunque ese día no le tocaba (§167).
final _colorFueraDeDia = Colors.teal.shade700;

/// Cómo le fue a una tienda ese día: su color y en palabras.
(Color, String) estadoEnElMapa(TiendaDeRuta t, {required bool yaPaso}) {
  if (t.fueraDeDia) return (_colorFueraDeDia, 'Fuera de su día');
  if (t.visita == 'venta') return (colorDeVendida, 'Vendida');
  if (t.visita == 'no_venta') return (_colorSinVenta, 'Visitada sin venta');
  if (t.reprogramada) return (colorDeReagendada, 'Reagendada para este día');
  return (rojoDistribucionesSE, yaPaso ? 'No se visitó' : 'Por visitar');
}

class PantallaMapaDeRutas extends ConsumerStatefulWidget {
  const PantallaMapaDeRutas({super.key, this.vendedorId});

  /// Con quién abre; sin él, el primero de la lista.
  final String? vendedorId;

  @override
  ConsumerState<PantallaMapaDeRutas> createState() => _EstadoMapaDeRutas();
}

class _EstadoMapaDeRutas extends ConsumerState<PantallaMapaDeRutas> {
  List<VendedorEnLista>? _vendedores;
  String? _vendedorId;
  late String _dia = _hoy;
  RutaDelVendedor? _ruta;
  bool _cargando = false;
  String? _error;

  String get _hoy => diaOperativoDe(ref.read(relojProvider)());

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargarVendedores());
  }

  Future<void> _cargarVendedores() async {
    final cliente = ref.read(clienteVendedoresProvider);
    if (cliente == null) {
      setState(
        () =>
            _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.',
      );
      return;
    }
    try {
      final lista = await cliente.lista();
      if (!mounted) return;
      final vendedores = [...lista.vendedores]
        ..sort((a, b) => a.nombre.compareTo(b.nombre));
      setState(() {
        _vendedores = vendedores;
        _vendedorId = vendedores.any((v) => v.id == widget.vendedorId)
            ? widget.vendedorId
            : vendedores.isEmpty
            ? null
            : vendedores.first.id;
      });
      await _cargarRuta();
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _cargarRuta() async {
    final cliente = ref.read(clienteVendedoresProvider);
    final id = _vendedorId;
    if (cliente == null || id == null) return;
    setState(() {
      _cargando = true;
      _error = null;
    });
    try {
      // Con la fecha y sin `dia`: el servidor toma su día de la semana y dice, de
      // ESA fecha, qué se visitó y qué se reagendó para ella.
      final ruta = await cliente.ruta(id, fecha: _dia);
      if (!mounted) return;
      setState(() {
        _ruta = ruta;
        _cargando = false;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _ruta = null;
        _cargando = false;
        _error = explicarErrorDeOficina(e);
      });
    }
  }

  void _irAlDia(String dia) {
    setState(() => _dia = dia);
    _cargarRuta();
  }

  Future<void> _calendario() async {
    final elegido = await elegirDiaEnCalendario(
      context,
      dia: _dia,
      hoy: _hoy,
      ayuda: '¿Qué día quieres ver?',
      // La ruta de los días que vienen también se puede ver: es el plan.
      diasAdelante: 60,
    );
    if (elegido != null) _irAlDia(elegido);
  }

  @override
  Widget build(BuildContext context) {
    final vendedores = _vendedores;
    final ruta = _ruta;
    final tiendas = ruta == null
        ? const <TiendaDeRuta>[]
        : ruta.tiendas.where((t) => t.tocaLaSemanaDe(_dia)).toList();
    final yaPaso = _dia.compareTo(_hoy) < 0;

    return Scaffold(
      key: const Key('pantalla_mapa_de_rutas'),
      appBar: AppBar(title: const Text('Mapa de rutas')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            child: vendedores == null
                ? const SizedBox.shrink()
                : DropdownButtonFormField<String>(
                    key: const Key('mapa_rutas_vendedor'),
                    initialValue: _vendedorId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Vendedor',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                    items: [
                      for (final v in vendedores)
                        DropdownMenuItem(
                          key: Key('mapa_rutas_vendedor_${v.id}'),
                          value: v.id,
                          child: Text(
                            v.rutas == null
                                ? v.nombre
                                : '${v.nombre} · ${v.rutas}',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (id) {
                      setState(() => _vendedorId = id);
                      _cargarRuta();
                    },
                  ),
          ),
          SelectorDeDia(
            dia: _dia,
            hoy: _hoy,
            sufijo: 'mapa_rutas',
            alIr: _irAlDia,
            alCalendario: _calendario,
            textoDeHoy: 'Ver hoy',
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                _error!,
                key: const Key('error_mapa_rutas'),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            )
          else if (vendedores != null && vendedores.isEmpty)
            const Padding(
              padding: EdgeInsets.all(12),
              child: Text('Todavía no hay vendedores.'),
            )
          else if (ruta != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text(
                _resumen(tiendas, yaPaso: yaPaso),
                key: const Key('resumen_mapa_rutas'),
                textAlign: TextAlign.center,
              ),
            ),
          if (_cargando) const LinearProgressIndicator(),
          const SizedBox(height: 4),
          Expanded(
            child: _Mapa(
              // Uno nuevo por vendedor y día: así encuadra sus tiendas.
              key: ValueKey('mapa_${_vendedorId}_$_dia'),
              tiendas: tiendas,
              yaPaso: yaPaso,
            ),
          ),
        ],
      ),
    );
  }

  String _resumen(List<TiendaDeRuta> tiendas, {required bool yaPaso}) {
    if (tiendas.isEmpty) return 'Ese día no le toca ninguna tienda.';
    // Las de fuera de su día van aparte: no son de las que le tocaban.
    final delDia = tiendas.where((t) => !t.fueraDeDia).toList();
    final vendidas = delDia.where((t) => t.visita == 'venta').length;
    final sinVenta = delDia.where((t) => t.visita == 'no_venta').length;
    final faltan = delDia.where((t) => t.visita == null).length;
    final reagendadas = delDia.where((t) => t.reprogramada).length;
    final fuera = tiendas.length - delDia.length;
    final sinUbicacion = tiendas.where((t) => !t.enElMapa).length;
    return [
      '${delDia.length} tienda(s)',
      if (vendidas > 0) '$vendidas vendida(s)',
      if (sinVenta > 0) '$sinVenta sin venta',
      if (faltan > 0) '$faltan ${yaPaso ? 'sin visitar' : 'por visitar'}',
      if (reagendadas > 0) '$reagendadas reagendada(s)',
      if (fuera > 0) '$fuera fuera de su día',
      if (sinUbicacion > 0) '$sinUbicacion sin ubicación (no salen)',
    ].join(' · ');
  }
}

class _Mapa extends ConsumerWidget {
  const _Mapa({super.key, required this.tiendas, required this.yaPaso});

  final List<TiendaDeRuta> tiendas;
  final bool yaPaso;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final enElMapa = tiendas.where((t) => t.enElMapa).toList();
    // La línea del recorrido: las de su día, en el orden de su hoja. Las
    // reagendadas y las de fuera de su día no son de ese orden.
    final recorrido =
        enElMapa.where((t) => !t.reprogramada && !t.fueraDeDia).toList()
          ..sort((a, b) {
            final ruta = a.ruta.compareTo(b.ruta);
            if (ruta != 0) return ruta;
            return (a.orden ?? 1 << 30).compareTo(b.orden ?? 1 << 30);
          });
    final guardado = ref.watch(mapaGuardadoProvider).valueOrNull;
    final capa = guardado == null
        ? null
        : ref.watch(
            capaDeCallesProvider((
              ruta: guardado.ruta,
              version: guardado.version,
            )),
          );
    final descarga = ref.watch(descargaDelMapaProvider);

    // El aviso de las calles va ARRIBA del mapa y no encima: en un teléfono
    // angosto tapaba las tiendas del norte, y no se podían tocar.
    return Column(
      children: [
        if (guardado == null || descarga is! DescargaInactiva)
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 0, 8, 4),
            child: AvisoDelMapa(estado: descarga, hayMapa: guardado != null),
          ),
        Expanded(
          child: Stack(
            children: [
              FlutterMap(
                options: MapOptions(
                  initialCameraFit: enElMapa.isEmpty
                      ? null
                      : CameraFit.coordinates(
                          coordinates: [
                            for (final t in enElMapa) LatLng(t.lat!, t.lng!),
                          ],
                          // Abajo va la leyenda: que ninguna tienda quede debajo.
                          padding: const EdgeInsets.fromLTRB(40, 40, 40, 100),
                          maxZoom: 16,
                        ),
                  initialCenter: _mazatlan,
                  initialZoom: 12,
                  minZoom: 9,
                  maxZoom: 18,
                  backgroundColor: const Color(0xFFF4F1EA),
                  interactionOptions: const InteractionOptions(
                    flags: InteractiveFlag.all & ~InteractiveFlag.rotate,
                  ),
                ),
                children: [
                  if (capa?.valueOrNull case final Widget calles) calles,
                  if (recorrido.length > 1)
                    PolylineLayer(
                      key: const Key('linea_de_la_ruta'),
                      polylines: [
                        Polyline(
                          points: [
                            for (final t in recorrido) LatLng(t.lat!, t.lng!),
                          ],
                          color: Colors.black45,
                          strokeWidth: 2.5,
                          pattern: StrokePattern.dashed(segments: const [8, 6]),
                        ),
                      ],
                    ),
                  MarkerLayer(
                    markers: [
                      for (final t in enElMapa)
                        Marker(
                          key: Key('pin_ruta_${t.id}'),
                          point: LatLng(t.lat!, t.lng!),
                          width: 30,
                          height: 30,
                          child: _Pin(
                            tienda: t,
                            color: estadoEnElMapa(t, yaPaso: yaPaso).$1,
                            alTocar: () => showModalBottomSheet<void>(
                              context: context,
                              showDragHandle: true,
                              builder: (_) => _Ficha(tienda: t, yaPaso: yaPaso),
                            ),
                          ),
                        ),
                    ],
                  ),
                ],
              ),
              Positioned(
                left: 8,
                bottom: 8,
                right: 8,
                child: _Leyenda(
                  yaPaso: yaPaso,
                  conCalles: capa?.valueOrNull != null,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// El círculo de la tienda, del color de cómo le fue, con su número en la hoja.
class _Pin extends StatelessWidget {
  const _Pin({
    required this.tienda,
    required this.color,
    required this.alTocar,
  });

  final TiendaDeRuta tienda;
  final Color color;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    label: tienda.nombre,
    child: GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: alTocar,
      child: DecoratedBox(
        key: Key('circulo_ruta_${tienda.id}'),
        decoration: BoxDecoration(
          color: color,
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 2),
          boxShadow: const [BoxShadow(color: Colors.black26, blurRadius: 3)],
        ),
        child: Center(
          child: Text(
            tienda.reprogramada || tienda.fueraDeDia
                ? '•'
                : '${tienda.orden ?? ''}',
            style: const TextStyle(
              color: Colors.white,
              fontSize: 11,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
      ),
    ),
  );
}

class _Ficha extends StatelessWidget {
  const _Ficha({required this.tienda, required this.yaPaso});

  final TiendaDeRuta tienda;
  final bool yaPaso;

  @override
  Widget build(BuildContext context) {
    final estilo = Theme.of(context).textTheme;
    final t = tienda;
    final (color, estado) = estadoEnElMapa(t, yaPaso: yaPaso);
    final detalle = [
      estado,
      if (t.fueraDeDia && t.visita == 'no_venta') 'visitada sin venta',
      if (t.importe != null) 'vendida \$${t.importe!.texto}',
    ].join(' · ');
    return SafeArea(
      child: Padding(
        key: Key('ficha_ruta_${t.id}'),
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(t.nombre, style: estilo.titleLarge),
            Text(
              [
                if (t.codigo != null) t.codigo!,
                if (t.ruta.isNotEmpty) t.ruta,
                if (t.orden != null && !t.reprogramada && !t.fueraDeDia)
                  'n.º ${t.orden}',
              ].join(' · '),
              style: estilo.bodyMedium,
            ),
            if (t.direccion != null)
              Text(t.direccion!, style: estilo.bodySmall),
            const SizedBox(height: 8),
            Text(
              detalle,
              key: const Key('estado_ficha_ruta'),
              style: TextStyle(color: color, fontWeight: FontWeight.w700),
            ),
          ],
        ),
      ),
    );
  }
}

class _Leyenda extends StatelessWidget {
  const _Leyenda({required this.yaPaso, required this.conCalles});

  final bool yaPaso;
  final bool conCalles;

  @override
  Widget build(BuildContext context) {
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
        key: const Key('leyenda_mapa_rutas'),
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
                  punto(colorDeVendida, 'Vendida'),
                  punto(_colorSinVenta, 'Sin venta'),
                  punto(
                    rojoDistribucionesSE,
                    yaPaso ? 'No se visitó' : 'Por visitar',
                  ),
                  punto(colorDeReagendada, 'Reagendada'),
                  punto(_colorFueraDeDia, 'Fuera de su día'),
                ],
              ),
              // La licencia de OpenStreetMap (ODbL) pide decir de dónde son
              // los datos de las calles.
              if (conCalles)
                Text(
                  'Calles © OpenStreetMap',
                  style: estilo?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
