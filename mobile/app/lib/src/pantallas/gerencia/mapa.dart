/// El mapa del día: dónde se vendió y dónde se perdió la visita.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ UN LIENZO Y NO UN MAPA CON CALLES
/// ─────────────────────────────────────────────────────────────────────────
/// Es la misma decisión que el radar de la pantalla de alta de cliente
/// (`vendedor/lienzo_espacial.dart`), por razones que aquí se suman:
///
/// · Un mapa con calles descarga mosaicos de un tercero. Este proyecto no mete
///   JavaScript ni servicios externos en el panel, y meterlos en la app sería
///   la misma decisión tomada al revés: una llave de API que mantener, una
///   factura que crece con el uso, y un dibujo que se queda en cuadros grises
///   cuando el gerente lo abre en la bodega sin cobertura.
///
/// · Y sobre todo: **la pregunta no necesita calles**. "¿Se está cubriendo la
///   zona o el vendedor se quedó en tres cuadras?" y "¿las visitas perdidas
///   están juntas en un rumbo?" se contestan con la forma de la nube de
///   puntos. Las calles no añaden nada a esas dos preguntas, y a cambio de
///   nada cuestan una dependencia externa en el camino crítico.
///
/// Lo que el lienzo NO puede contestar es "¿qué tienda es este punto?" sin
/// tocarlo. Por eso cada punto es tocable y muestra el nombre del cliente, el
/// importe o el motivo, y la hora.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA PROYECCIÓN
/// ─────────────────────────────────────────────────────────────────────────
/// Equirectangular centrada en el promedio de los puntos, con el coseno de la
/// latitud aplicado a la longitud. Sin esa corrección, a 20° de latitud un
/// grado de longitud se dibujaría igual de ancho que uno de latitud y la nube
/// saldría estirada un 6% en horizontal — suficiente para que dos puntos que
/// están uno encima del otro parezcan separados.
library;

import 'dart:math' as math;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_tablero.dart';
import '../../estado/tablero.dart';
import 'comunes.dart';

class PantallaMapa extends ConsumerStatefulWidget {
  const PantallaMapa({super.key});

  @override
  ConsumerState<PantallaMapa> createState() => _EstadoMapa();
}

class _EstadoMapa extends ConsumerState<PantallaMapa> {
  PuntoDelMapa? _seleccionado;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(mapaProvider.notifier).cargar();
    });
  }

  @override
  Widget build(BuildContext context) {
    final estado = ref.watch(mapaProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Mapa del día'),
        actions: [
          IconButton(
            key: const Key('boton_refrescar_mapa'),
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.read(mapaProvider.notifier).cargar(),
          ),
        ],
      ),
      body: switch (estado) {
        MapaCargando() => const Center(
            key: Key('mapa_cargando'),
            child: CircularProgressIndicator(),
          ),
        MapaSinNada(:final motivo) => Padding(
            key: const Key('mapa_sin_nada'),
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.cloud_off_outlined, size: 48),
                const SizedBox(height: 16),
                Text(motivo, textAlign: TextAlign.center),
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: () => ref.read(mapaProvider.notifier).cargar(),
                  child: const Text('Volver a intentar'),
                ),
              ],
            ),
          ),
        MapaListo(:final local) => _Cuerpo(
            local: local,
            seleccionado: _seleccionado,
            onTocar: (p) => setState(() => _seleccionado = p),
          ),
      },
    );
  }
}

class _Cuerpo extends StatelessWidget {
  const _Cuerpo({
    required this.local,
    required this.seleccionado,
    required this.onTocar,
  });

  final MapaLocal local;
  final PuntoDelMapa? seleccionado;
  final void Function(PuntoDelMapa) onTocar;

  @override
  Widget build(BuildContext context) {
    final mapa = local.mapa;
    if (mapa.puntos.isEmpty) {
      return const Center(
        key: Key('mapa_vacio'),
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text(
            'Todavía no hay visitas con coordenadas este día.\n\n'
            'Las ventas sin GPS no aparecen aquí a propósito: dibujarlas en '
            'algún lado sería inventarles una ubicación.',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    final esquema = Theme.of(context).colorScheme;
    return ListView(
      key: const Key('mapa_del_dia'),
      padding: const EdgeInsets.all(16),
      children: [
        Text(
          '${mapa.ventas} ventas y ${mapa.perdidas} visitas perdidas '
          'con ubicación · bajado ${antiguedadEnPalabras(local.recibidoEn)}'
          '${local.deLaCopia ? ' (copia local, sin conexión)' : ''}.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        if (mapa.recortados)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              // Un mapa recortado en silencio haría que alguien contara
              // visitas sobre el dibujo y le faltaran.
              'El servidor dejó puntos fuera: hay más visitas de las que caben '
              'en un mapa de teléfono. Lo que ves es una parte del día.',
              style: TextStyle(fontSize: 12, color: esquema.error),
            ),
          ),
        const SizedBox(height: 12),
        AspectRatio(
          aspectRatio: 1,
          child: LienzoDeVisitas(
            puntos: mapa.puntos,
            seleccionado: seleccionado,
            onTocar: onTocar,
          ),
        ),
        const SizedBox(height: 12),
        if (seleccionado != null)
          _Detalle(punto: seleccionado!)
        else
          Text(
            'Toca un punto para ver de qué cliente es.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        const SizedBox(height: 16),
        Row(
          children: [
            _Leyenda(color: esquema.primary, texto: 'venta'),
            const SizedBox(width: 16),
            _Leyenda(color: esquema.error, texto: 'visita perdida'),
          ],
        ),
      ],
    );
  }
}

class _Leyenda extends StatelessWidget {
  const _Leyenda({required this.color, required this.texto});

  final Color color;
  final String texto;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Container(
            width: 12,
            height: 12,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(texto, style: Theme.of(context).textTheme.bodySmall),
        ],
      );
}

class _Detalle extends StatelessWidget {
  const _Detalle({required this.punto});

  final PuntoDelMapa punto;

  @override
  Widget build(BuildContext context) {
    final hora =
        '${punto.momento.hour.toString().padLeft(2, '0')}:'
        '${punto.momento.minute.toString().padLeft(2, '0')}';
    return Card(
      key: const Key('detalle_del_punto'),
      margin: EdgeInsets.zero,
      child: ListTile(
        leading: Icon(
          punto.esVenta ? Icons.shopping_basket_outlined : Icons.block_outlined,
          color: punto.esVenta
              ? Theme.of(context).colorScheme.primary
              : Theme.of(context).colorScheme.error,
        ),
        title: Text(punto.cliente),
        subtitle: Text(
          punto.esVenta
              ? '${pesos(punto.importe ?? Dinero.cero)} · $hora'
                  '${punto.vendedor == null ? '' : ' · ${punto.vendedor}'}'
              : '${punto.motivo ?? 'sin motivo'} · $hora'
                  '${punto.vendedor == null ? '' : ' · ${punto.vendedor}'}',
        ),
      ),
    );
  }
}

/// El lienzo. Público para poder probarlo sin la pantalla completa.
class LienzoDeVisitas extends StatelessWidget {
  const LienzoDeVisitas({
    super.key,
    required this.puntos,
    this.seleccionado,
    this.onTocar,
  });

  final List<PuntoDelMapa> puntos;
  final PuntoDelMapa? seleccionado;
  final void Function(PuntoDelMapa)? onTocar;

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    final proyeccion = Proyeccion.de(puntos);

    return Semantics(
      // Sin esto un lector de pantalla solo anuncia "imagen".
      label: 'Mapa de ${puntos.length} visitas del día: '
          '${puntos.where((p) => p.esVenta).length} con venta y '
          '${puntos.where((p) => !p.esVenta).length} perdidas.',
      child: LayoutBuilder(
        builder: (_, limites) => GestureDetector(
          onTapDown: onTocar == null
              ? null
              : (detalle) {
                  final cercano = proyeccion.masCercano(
                    detalle.localPosition,
                    Size(limites.maxWidth, limites.maxHeight),
                    puntos,
                  );
                  if (cercano != null) onTocar!(cercano);
                },
          child: CustomPaint(
            size: Size(limites.maxWidth, limites.maxHeight),
            painter: _PintorDelMapa(
              puntos: puntos,
              proyeccion: proyeccion,
              seleccionado: seleccionado,
              colorVenta: esquema.primary,
              colorPerdida: esquema.error,
              colorFondo: esquema.surfaceContainerHighest,
              colorLinea: esquema.outlineVariant,
            ),
          ),
        ),
      ),
    );
  }
}

/// Convierte lat/lng a píxeles del lienzo.
///
/// Se extrae a una clase propia porque la usan el pintor y la detección del
/// toque, y si cada uno hiciera su propio cálculo bastaría una diferencia de un
/// píxel para que el punto que se toca no sea el que se ve.
class Proyeccion {
  Proyeccion._({
    required this.latCentro,
    required this.lngCentro,
    required this.semiAltoGrados,
    required this.semiAnchoGrados,
  });

  factory Proyeccion.de(List<PuntoDelMapa> puntos) {
    if (puntos.isEmpty) {
      return Proyeccion._(
        latCentro: 0,
        lngCentro: 0,
        semiAltoGrados: 0.01,
        semiAnchoGrados: 0.01,
      );
    }
    var latMin = puntos.first.lat;
    var latMax = puntos.first.lat;
    var lngMin = puntos.first.lng;
    var lngMax = puntos.first.lng;
    for (final p in puntos) {
      latMin = math.min(latMin, p.lat);
      latMax = math.max(latMax, p.lat);
      lngMin = math.min(lngMin, p.lng);
      lngMax = math.max(lngMax, p.lng);
    }
    final latCentro = (latMin + latMax) / 2;
    final lngCentro = (lngMin + lngMax) / 2;

    // El coseno de la latitud: a 20° un grado de longitud mide ~94% de lo que
    // mide uno de latitud. Sin esto la nube sale estirada en horizontal.
    final coseno = math.cos(latCentro * math.pi / 180).abs().clamp(0.1, 1.0);

    // Un semilado mínimo para que dos visitas de la misma cuadra no se
    // dibujen en las esquinas opuestas del lienzo: con un rango de 20 metros,
    // escalar al lienzo completo convertiría el ruido del GPS en un mapa.
    const minimoGrados = 0.0025; // ~275 m
    final semiAlto =
        math.max((latMax - latMin) / 2 * 1.2, minimoGrados).toDouble();
    final semiAnchoReal = (lngMax - lngMin) / 2 * coseno * 1.2;
    final semiAncho = math.max(semiAnchoReal, minimoGrados).toDouble();

    // El lienzo es cuadrado: se toma el mayor de los dos para que la escala sea
    // la MISMA en los dos ejes. Con escalas distintas, una ruta que avanza en
    // línea recta de norte a sur se dibujaría como una diagonal.
    final semi = math.max(semiAlto, semiAncho);
    return Proyeccion._(
      latCentro: latCentro,
      lngCentro: lngCentro,
      semiAltoGrados: semi,
      semiAnchoGrados: semi / coseno,
    );
  }

  final double latCentro;
  final double lngCentro;
  final double semiAltoGrados;
  final double semiAnchoGrados;

  Offset aPixeles(PuntoDelMapa punto, Size tamano) {
    final dx = (punto.lng - lngCentro) / semiAnchoGrados;
    // La latitud crece hacia el norte y el eje Y del lienzo hacia abajo.
    final dy = -(punto.lat - latCentro) / semiAltoGrados;
    return Offset(
      tamano.width / 2 + dx * tamano.width / 2 * 0.88,
      tamano.height / 2 + dy * tamano.height / 2 * 0.88,
    );
  }

  /// El punto más cercano al toque, si cayó razonablemente cerca.
  ///
  /// El umbral es generoso (28 px) porque un dedo no apunta a un círculo de 6
  /// píxeles. Y devuelve `null` si no hay nada cerca, en vez de seleccionar el
  /// menos lejano del mapa: tocar una esquina vacía no debería abrir la ficha
  /// de una tienda que está al otro lado.
  PuntoDelMapa? masCercano(
    Offset toque,
    Size tamano,
    List<PuntoDelMapa> puntos,
  ) {
    PuntoDelMapa? mejor;
    var mejorDistancia = double.infinity;
    for (final p in puntos) {
      final distancia = (aPixeles(p, tamano) - toque).distance;
      if (distancia < mejorDistancia) {
        mejorDistancia = distancia;
        mejor = p;
      }
    }
    return mejorDistancia <= 28 ? mejor : null;
  }
}

class _PintorDelMapa extends CustomPainter {
  const _PintorDelMapa({
    required this.puntos,
    required this.proyeccion,
    required this.seleccionado,
    required this.colorVenta,
    required this.colorPerdida,
    required this.colorFondo,
    required this.colorLinea,
  });

  final List<PuntoDelMapa> puntos;
  final Proyeccion proyeccion;
  final PuntoDelMapa? seleccionado;
  final Color colorVenta;
  final Color colorPerdida;
  final Color colorFondo;
  final Color colorLinea;

  @override
  void paint(Canvas lienzo, Size tamano) {
    lienzo.drawRRect(
      RRect.fromRectAndRadius(
        Offset.zero & tamano,
        const Radius.circular(12),
      ),
      Paint()..color = colorFondo,
    );

    // Una cruz tenue por el centro: da referencia de rumbo sin pretender ser
    // un mapa. Sin ninguna referencia, una nube de puntos flotando no se sabe
    // leer de qué lado está el norte.
    final guia = Paint()
      ..color = colorLinea
      ..strokeWidth = 1;
    lienzo.drawLine(
      Offset(tamano.width / 2, 8),
      Offset(tamano.width / 2, tamano.height - 8),
      guia,
    );
    lienzo.drawLine(
      Offset(8, tamano.height / 2),
      Offset(tamano.width - 8, tamano.height / 2),
      guia,
    );
    _texto(lienzo, 'N', Offset(tamano.width / 2 + 5, 6), colorLinea);

    // Las perdidas se pintan DESPUÉS de las ventas: en una zona donde se vendió
    // mucho y se perdió poco, lo poco es lo que hay que ver, y pintarlo primero
    // lo dejaría tapado.
    for (final p in puntos.where((p) => p.esVenta)) {
      _punto(lienzo, p, tamano, colorVenta);
    }
    for (final p in puntos.where((p) => !p.esVenta)) {
      _punto(lienzo, p, tamano, colorPerdida);
    }
  }

  void _punto(Canvas lienzo, PuntoDelMapa punto, Size tamano, Color color) {
    final centro = proyeccion.aPixeles(punto, tamano);
    final esSeleccionado = punto == seleccionado;
    lienzo.drawCircle(
      centro,
      esSeleccionado ? 9 : 5,
      Paint()..color = color.withValues(alpha: esSeleccionado ? 1 : 0.75),
    );
    if (esSeleccionado) {
      lienzo.drawCircle(
        centro,
        13,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
    }
  }

  void _texto(Canvas lienzo, String texto, Offset donde, Color color) {
    final pintor = TextPainter(
      text: TextSpan(
        text: texto,
        style: TextStyle(color: color, fontSize: 11),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    pintor.paint(lienzo, donde);
  }

  @override
  bool shouldRepaint(_PintorDelMapa anterior) =>
      anterior.puntos != puntos || anterior.seleccionado != seleccionado;
}
