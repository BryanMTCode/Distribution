/// Cómo llegar a un cliente, sin mapa y sin señal (ADR 0002 §129).
///
/// «El vendedor operará en zonas sin señal. No podemos depender de APIs de
/// mapas dentro de la app que consuman datos, se queden en blanco sin internet,
/// o inflen el APK con SDKs pesados.»
///
/// Dos caminos, y los dos sin descargar nada:
///
///   · **La flecha**, aquí mismo: con el GPS —que no usa datos— y la coordenada
///     de la tienda que ya está en el teléfono, a cuánto está y hacia dónde. Si
///     el vendedor va en marcha, «de frente», «a tu derecha»; quieto, contra el
///     norte. Es una línea recta, no una ruta por calles: sirve para no perderse
///     en una colonia y para encontrar la puerta en la última cuadra.
///   · **«Abrir en Mapas»**: la coordenada, a la app de mapas que el teléfono ya
///     tiene, que sí sabe de calles. Sin señal funciona si esa app tiene el mapa
///     de la zona descargado (se hace una vez, con WiFi).
library;

import 'dart:async';
import 'dart:math' as math;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/abridor_de_mapas.dart';
import '../../datos/repo_clientes.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart';
import 'comunes.dart';
import 'mapa.dart';

final abridorDeMapasProvider = Provider<AbridorDeMapas>(
  (_) => const AbridorDeMapasDelSistema(),
);

/// Abre la tienda en la app de mapas del teléfono, y si no hay ninguna lo dice.
Future<void> abrirEnMapas(BuildContext context, WidgetRef ref, ClienteEnRuta cliente) async {
  final mensajero = ScaffoldMessenger.of(context);
  final abrio = await ref.read(abridorDeMapasProvider).abrir(
        lat: cliente.lat!,
        lng: cliente.lng!,
        nombre: cliente.nombreComercial,
      );
  if (!abrio) {
    mensajero.showSnackBar(const SnackBar(
      content: Text('Este teléfono no tiene una app de mapas. Usa «Guíame»: la '
          'flecha no necesita mapa.'),
      duration: Duration(seconds: 6),
    ));
  }
}

class PantallaGuia extends ConsumerStatefulWidget {
  const PantallaGuia({super.key, required this.cliente});

  final ClienteEnRuta cliente;

  @override
  ConsumerState<PantallaGuia> createState() => _EstadoGuia();
}

class _EstadoGuia extends ConsumerState<PantallaGuia> {
  StreamSubscription<LecturaGps>? _lecturas;
  LecturaGps? _ultima;

  /// El último rumbo de marcha bueno: en un alto, el GPS deja de darlo y la
  /// flecha no debe brincar al norte cada semáforo.
  double? _rumboConocido;
  DateTime? _rumboConocidoEn;

  @override
  void initState() {
    super.initState();
    if (widget.cliente.conUbicacion) _seguir();
  }

  /// Empieza (o vuelve a empezar, tras prender la ubicación o dar el permiso) a
  /// escuchar al GPS.
  void _seguir() {
    _lecturas?.cancel();
    _lecturas = ref.read(servicioUbicacionProvider).seguir().listen((lectura) {
      if (!mounted) return;
      setState(() => _ultima = lectura);
    });
  }

  void _reintentar() {
    setState(() => _ultima = null);
    _seguir();
  }

  @override
  void dispose() {
    _lecturas?.cancel();
    super.dispose();
  }

  Guia? _guia() {
    final l = _ultima;
    if (l is! GpsObtenido) return null;
    final c = widget.cliente;
    var rumbo = l.rumboDeMarcha;
    var velocidad = l.velocidad;
    final ahora = DateTime.now();
    if (rumbo != null && (velocidad ?? 0) >= velocidadMinimaParaOrientar) {
      _rumboConocido = rumbo;
      _rumboConocidoEn = ahora;
    } else if (_rumboConocido != null &&
        _rumboConocidoEn != null &&
        ahora.difference(_rumboConocidoEn!) < const Duration(seconds: 20)) {
      // Un alto breve: se sigue mirando hacia donde se iba.
      rumbo = _rumboConocido;
      velocidad = velocidadMinimaParaOrientar;
    }
    return Guia.calcular(
      desdeLat: l.ubicacion.lat,
      desdeLng: l.ubicacion.lng,
      hastaLat: c.lat!,
      hastaLng: c.lng!,
      rumboDeMarcha: rumbo,
      velocidad: velocidad,
      precisionMetros: l.ubicacion.precisionMetros,
    );
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.cliente;
    final estilo = Theme.of(context).textTheme;
    return Scaffold(
      key: const Key('pantalla_guia'),
      appBar: AppBar(title: Text(c.nombreComercial)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (c.direccion != null) Text(c.direccion!, style: estilo.bodyMedium),
          if (c.referencias != null) Text(c.referencias!, style: estilo.bodySmall),
          const SizedBox(height: 12),
          if (!c.conUbicacion)
            const Aviso(
              'Esta tienda todavía no tiene ubicación. Pónsela en su perfil la '
              'próxima vez que estés en la puerta.',
              grave: true,
            )
          else ...[
            _cuerpo(context),
            const SizedBox(height: 20),
            // El mapa de la app, con la tienda y el vendedor (ADR 0002 §130).
            FilledButton.tonalIcon(
              key: const Key('guia_ver_en_mapa'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => PantallaMapa(destino: c)),
              ),
              icon: const Icon(Icons.map),
              label: const Text('Ver en el mapa'),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              key: const Key('abrir_en_mapas'),
              onPressed: () => abrirEnMapas(context, ref, c),
              icon: const Icon(Icons.map_outlined),
              label: const Text('Abrir en Mapas (ruta por calles)'),
            ),
            const SizedBox(height: 6),
            Text(
              '«Ver en el mapa» usa el mapa de la app, que se baja una vez con WiFi. '
              '«Abrir en Mapas» usa la app que tengas (Google Maps, Waze, Organic '
              'Maps), que da la ruta por calles; sin señal funciona si descargas una '
              'vez el mapa de Mazatlán en esa app. La flecha de arriba no necesita '
              'mapa.',
              style: estilo.bodySmall,
            ),
          ],
        ],
      ),
    );
  }

  Widget _cuerpo(BuildContext context) {
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    final lectura = _ultima;
    switch (lectura) {
      case null || GpsSinLectura():
        return Column(
          key: const Key('guia_buscando'),
          children: [
            const SizedBox(height: 24),
            const CircularProgressIndicator(),
            const SizedBox(height: 16),
            Text('Buscando tu ubicación…', style: estilo.titleMedium),
            const SizedBox(height: 6),
            const Text(
              'El GPS no necesita señal de datos. Afuera y sin techo encuentra los '
              'satélites más rápido.',
              textAlign: TextAlign.center,
            ),
          ],
        );
      case GpsApagado():
        return _conReintento(const Aviso(
          'La ubicación del teléfono está apagada. Préndela (en la barra de arriba) '
          'y toca «Volver a intentar».',
          grave: true,
        ));
      case GpsSinPermiso(:final definitivo):
        return _conReintento(Aviso(
          definitivo
              ? 'La app no tiene permiso de ubicación. Dáselo en Ajustes → Apps → '
                  'Distribuciones SE → Permisos, y toca «Volver a intentar».'
              : 'Sin permiso de ubicación la flecha no sabe dónde estás. Toca «Volver '
                  'a intentar» y acepta el permiso.',
          grave: true,
        ));
      case GpsObtenido():
        break;
    }
    final guia = _guia()!;
    final precision = lectura.ubicacion.precisionMetros;
    if (guia.llegaste) {
      return Column(
        key: const Key('guia_llegaste'),
        children: [
          const SizedBox(height: 12),
          Icon(Icons.storefront, size: 120, color: colores.primary),
          Text('Ya llegaste', style: estilo.headlineMedium),
          Text('La tienda está a ${guia.distanciaTexto}, '
              'tan cerca como el GPS sabe decir.'),
          const SizedBox(height: 12),
          FilledButton(
            key: const Key('guia_volver'),
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Volver a la lista'),
          ),
        ],
      );
    }
    final texto = guia.orientadaPorMarcha
        ? guia.indicacion!.texto
        : 'al ${guia.rumboTexto}';
    return Semantics(
      label: 'La tienda está a ${guia.distanciaTexto}, $texto',
      child: Column(
        key: const Key('guia_flecha'),
        children: [
          Text(guia.distanciaTexto, key: const Key('guia_distancia'),
              style: estilo.displayMedium?.copyWith(fontWeight: FontWeight.w700)),
          Text(texto, key: const Key('guia_hacia'), style: estilo.headlineSmall),
          const SizedBox(height: 12),
          SizedBox(
            height: 220,
            child: Stack(
              alignment: Alignment.center,
              children: [
                Container(
                  width: 220,
                  height: 220,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: colores.surfaceContainerHighest,
                  ),
                ),
                // Quieto, la flecha va contra el norte: la «N» arriba lo dice.
                if (!guia.orientadaPorMarcha)
                  Positioned(
                    top: 6,
                    child: Text('N', key: const Key('guia_norte'),
                        style: estilo.titleMedium?.copyWith(
                            color: colores.error, fontWeight: FontWeight.w700)),
                  ),
                Transform.rotate(
                  angle: guia.anguloDeLaFlecha * math.pi / 180,
                  child: Icon(Icons.navigation, key: const Key('guia_icono_flecha'),
                      size: 150, color: colores.primary),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          Text(
            guia.orientadaPorMarcha
                ? 'La flecha va hacia donde vas: arriba es de frente.'
                : 'Arriba es el norte. Camina o avanza unos pasos y la flecha se '
                    'orienta hacia donde vas.',
            key: const Key('guia_explicacion'),
            textAlign: TextAlign.center,
          ),
          if (precision != null)
            Text('GPS ±${precision.round()} m · en línea recta, no por calles',
                style: estilo.bodySmall),
        ],
      ),
    );
  }

  Widget _conReintento(Widget aviso) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          aviso,
          const SizedBox(height: 8),
          FilledButton.tonalIcon(
            key: const Key('guia_reintentar'),
            onPressed: _reintentar,
            icon: const Icon(Icons.refresh),
            label: const Text('Volver a intentar'),
          ),
        ],
      );
}
