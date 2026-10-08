/// La ubicación de un cliente: con el GPS o escrita a mano (ADR 0002 §84).
///
/// Las dos formas a la vez, en el mismo lugar:
///
///   · **Tomar con el GPS**, si se está parado en el negocio. Llena los campos
///     con lo que leyó el satélite y dice qué tan buena fue la lectura.
///   · **Los campos de latitud y longitud**, para meterla o corregirla a mano:
///     copiada de un mapa —se puede pegar «23.2494, -106.4111» en uno solo—,
///     dictada por teléfono, o porque dentro del mercado el GPS no leyó.
///
/// Escribir en un campo vuelve la ubicación «manual»: la precisión del GPS ya
/// no dice nada de un número que alguien cambió.
///
/// Lo usan el perfil del cliente en el teléfono del vendedor (sin señal, por la
/// cola) y la ficha del cliente en la app de la oficina (en línea).
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/servicio_ubicacion.dart';
import '../estado/alta.dart';

class EditorDeUbicacion extends ConsumerStatefulWidget {
  const EditorDeUbicacion({
    super.key,
    required this.alGuardar,
    this.lat,
    this.lng,
    this.origen,
    this.habilitado = true,
  });

  final double? lat;
  final double? lng;
  final String? origen;
  final bool habilitado;

  /// Guarda la ubicación. Devuelve el mensaje para la pantalla; si truena, el
  /// texto del error se muestra tal cual.
  final Future<String> Function(Ubicacion ubicacion) alGuardar;

  @override
  ConsumerState<EditorDeUbicacion> createState() => _EstadoEditor();
}

class _EstadoEditor extends ConsumerState<EditorDeUbicacion> {
  late final _lat = TextEditingController(text: widget.lat?.toStringAsFixed(7) ?? '');
  late final _lng = TextEditingController(text: widget.lng?.toStringAsFixed(7) ?? '');
  OrigenUbicacion _origen = OrigenUbicacion.manual;
  double? _precision;
  bool _leyendo = false;
  bool _guardando = false;
  String? _aviso;
  bool _avisoEsError = false;

  @override
  void initState() {
    super.initState();
    if (widget.origen == 'gps') _origen = OrigenUbicacion.gps;
  }

  @override
  void dispose() {
    _lat.dispose();
    _lng.dispose();
    super.dispose();
  }

  void _avisar(String texto, {bool error = false}) => setState(() {
        _aviso = texto;
        _avisoEsError = error;
      });

  Future<void> _tomarGps() async {
    setState(() => _leyendo = true);
    final lectura = await ref.read(servicioUbicacionProvider).leer();
    if (!mounted) return;
    setState(() => _leyendo = false);
    switch (lectura) {
      case GpsObtenido(:final ubicacion):
        _lat.text = ubicacion.latTexto;
        _lng.text = ubicacion.lngTexto;
        setState(() {
          _origen = OrigenUbicacion.gps;
          _precision = ubicacion.precisionMetros;
        });
        final calidad = ubicacion.calidad;
        _avisar(
          _precision == null
              ? 'Lectura del GPS lista. Revísala y guarda.'
              : 'Lectura del GPS: ±${_precision!.round()} m'
                  '${calidad.convieneAjustar ? '. Es poco precisa: si sabes la buena, corrígela abajo.' : '. Guárdala.'}',
          error: calidad.convieneAjustar,
        );
      case GpsSinPermiso(:final definitivo):
        _avisar(
          definitivo
              ? 'El teléfono no deja usar la ubicación. Actívala en los ajustes, o '
                  'escríbela a mano.'
              : 'Hace falta el permiso de ubicación. Vuelve a tocar el botón, o '
                  'escríbela a mano.',
          error: true,
        );
      case GpsApagado():
        _avisar('La ubicación del teléfono está apagada. Préndela, o escríbela a mano.',
            error: true);
      case GpsSinLectura():
        _avisar('El GPS no leyó a tiempo (pasa bajo techo). Sal a la banqueta e '
            'inténtalo otra vez, o escríbela a mano.', error: true);
    }
  }

  /// Escribir en un campo vuelve la ubicación manual. Pegar «lat, lng» en uno
  /// solo la reparte en los dos.
  void _alEscribir(TextEditingController campo, String texto) {
    final par = separarCoordenadas(texto);
    if (par != null) {
      _lat.text = par.$1;
      _lng.text = par.$2;
    }
    if (_origen != OrigenUbicacion.manual || _precision != null) {
      setState(() {
        _origen = OrigenUbicacion.manual;
        _precision = null;
      });
    }
  }

  Future<void> _guardar() async {
    final Ubicacion ubicacion;
    try {
      final leida = leerCoordenadas(_lat.text, _lng.text, origen: _origen);
      ubicacion = Ubicacion(
        lat: leida.lat,
        lng: leida.lng,
        origen: _origen,
        precisionMetros: _origen == OrigenUbicacion.gps ? _precision : null,
      );
    } on UbicacionInvalida catch (e) {
      _avisar(e.mensaje, error: true);
      return;
    }
    setState(() => _guardando = true);
    try {
      final mensaje = await widget.alGuardar(ubicacion);
      if (!mounted) return;
      setState(() => _guardando = false);
      _avisar(mensaje);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _guardando = false);
      _avisar('$e', error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final activo = widget.habilitado && !_guardando && !_leyendo;
    return Column(
      key: const Key('editor_de_ubicacion'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OutlinedButton.icon(
          key: const Key('boton_gps_ubicacion'),
          onPressed: activo ? _tomarGps : null,
          icon: _leyendo
              ? const SizedBox(
                  width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.my_location),
          label: const Text('Tomar con el GPS (parado en el negocio)'),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: TextField(
                key: const Key('campo_latitud'),
                controller: _lat,
                enabled: activo,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true, signed: true),
                onChanged: (t) => _alEscribir(_lat, t),
                decoration: const InputDecoration(
                  labelText: 'Latitud',
                  hintText: '23.2494',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                key: const Key('campo_longitud'),
                controller: _lng,
                enabled: activo,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true, signed: true),
                onChanged: (t) => _alEscribir(_lng, t),
                decoration: const InputDecoration(
                  labelText: 'Longitud',
                  hintText: '-106.4111',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          _origen == OrigenUbicacion.gps
              ? 'Tomada con el GPS${_precision == null ? '' : ' (±${_precision!.round()} m)'}.'
              : 'Escrita a mano. Puedes pegar «23.2494, -106.4111» tal como lo copia el mapa.',
          key: const Key('origen_ubicacion'),
          style: Theme.of(context).textTheme.bodySmall,
        ),
        if (_aviso != null) ...[
          const SizedBox(height: 8),
          Text(
            _aviso!,
            key: const Key('aviso_ubicacion'),
            style: TextStyle(color: _avisoEsError ? colores.error : null),
          ),
        ],
        const SizedBox(height: 8),
        FilledButton.icon(
          key: const Key('boton_guardar_ubicacion'),
          onPressed: activo ? _guardar : null,
          icon: const Icon(Icons.save_outlined),
          label: const Text('Guardar ubicación'),
        ),
      ],
    );
  }
}
