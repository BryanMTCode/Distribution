/// Alta de cliente en la calle.
///
/// Dos decisiones que definen esta pantalla:
///
/// 1. **El GPS no bloquea.** La tienda existe aunque el satélite no colabore.
///    Se puede guardar sin coordenadas; el estado de la lectura se muestra, no
///    se impone.
///
/// 2. **La corrección manual es sin mapa.** Un mapa con mosaicos necesita red,
///    y la corrección se hace justo donde no la hay. Con desplazamientos
///    cardinales el vendedor acerca el punto a la puerta del negocio dentro de
///    un mercado techado, sin descargar nada.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart';
import '../../estado/sesion.dart';

class PantallaAltaCliente extends ConsumerStatefulWidget {
  const PantallaAltaCliente({super.key});

  @override
  ConsumerState<PantallaAltaCliente> createState() => _EstadoAlta();
}

class _EstadoAlta extends ConsumerState<PantallaAltaCliente> {
  final _formulario = GlobalKey<FormState>();
  final _nombre = TextEditingController();
  final _telefono = TextEditingController();
  final _calle = TextEditingController();
  final _numero = TextEditingController();
  final _colonia = TextEditingController();
  final _referencias = TextEditingController();

  /// El vendedor ya vio el aviso de cercanos y confirmó que es otra tienda.
  bool _confirmoQueEsNueva = false;

  @override
  void initState() {
    super.initState();
    // Se pide el GPS al abrir: mientras el vendedor teclea el nombre, el
    // satélite va fijando. Pedirlo al guardar lo haría esperar.
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => ref.read(ubicacionProvider.notifier).leer(),
    );
  }

  @override
  void dispose() {
    for (final c in [_nombre, _telefono, _calle, _numero, _colonia, _referencias]) {
      c.dispose();
    }
    super.dispose();
  }

  void _guardar() {
    if (!(_formulario.currentState?.validate() ?? false)) return;

    final cercanos = ref.read(cercanosProvider);
    if (cercanos.isNotEmpty && !_confirmoQueEsNueva) {
      // No se bloquea: se pide una confirmación explícita. En la calle el
      // vendedor sabe si la tienda de al lado es la misma; en la oficina, dos
      // semanas después y con dos historiales ya separados, nadie puede saberlo.
      setState(() {});
      return;
    }

    ref.read(altasProvider).registrar(
          DatosDeAlta(
            nombreComercial: _nombre.text,
            telefono: _telefono.text,
            calle: _calle.text,
            numero: _numero.text,
            colonia: _colonia.text,
            referencias: _referencias.text,
            ubicacion: ref.read(ubicacionProvider).ubicacion,
          ),
        );

    ref.invalidate(clientesProvider);
    ref.invalidate(resumenColaProvider);

    if (!mounted) return;
    Navigator.of(context).pop();
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(
        const SnackBar(content: Text('Cliente guardado. Se enviará al haber señal.')),
      );
  }

  @override
  Widget build(BuildContext context) {
    final cercanos = ref.watch(cercanosProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Nuevo cliente')),
      body: Form(
        key: _formulario,
        child: ListView(
          // Aire abajo: sin él, los últimos controles quedan pegados a la barra
          // de guardar y se vuelven difíciles de tocar con el pulgar.
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: [
            TextFormField(
              key: const Key('campo_nombre'),
              controller: _nombre,
              autofocus: true,
              textCapitalization: TextCapitalization.words,
              decoration: const InputDecoration(
                labelText: 'Nombre del negocio *',
                border: OutlineInputBorder(),
              ),
              validator: (v) =>
                  (v ?? '').trim().isEmpty ? 'Escribe el nombre del negocio' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const Key('campo_telefono'),
              controller: _telefono,
              keyboardType: TextInputType.phone,
              decoration: const InputDecoration(
                labelText: 'Teléfono',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  flex: 3,
                  child: TextFormField(
                    key: const Key('campo_calle'),
                    controller: _calle,
                    decoration: const InputDecoration(
                      labelText: 'Calle',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextFormField(
                    key: const Key('campo_numero'),
                    controller: _numero,
                    decoration: const InputDecoration(
                      labelText: 'Núm.',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const Key('campo_colonia'),
              controller: _colonia,
              decoration: const InputDecoration(
                labelText: 'Colonia',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const Key('campo_referencias'),
              controller: _referencias,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: 'Referencias',
                hintText: 'Frente al parque, portón café',
                helperText: 'En colonias sin nomenclatura es lo que sirve para volver',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 20),
            const _BloqueUbicacion(),
            if (cercanos.isNotEmpty) ...[
              const SizedBox(height: 16),
              _AvisoCercanos(
                cercanos: cercanos,
                confirmado: _confirmoQueEsNueva,
                alConfirmar: () => setState(() => _confirmoQueEsNueva = true),
              ),
            ],
            const SizedBox(height: 24),
          ],
        ),
      ),
      // El guardar va en una barra fija, no al final del formulario: el
      // vendedor captura de pie, con el cliente enfrente, y obligarlo a
      // desplazarse para encontrar el botón es la clase de fricción que
      // termina en "mejor lo apunto en papel".
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  key: const Key('boton_guardar'),
                  onPressed: _guardar,
                  child: const Padding(
                    padding: EdgeInsets.symmetric(vertical: 12),
                    child: Text('Guardar cliente'),
                  ),
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'Se guarda en el teléfono y se envía al haber señal.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Estado del GPS y corrección manual.
class _BloqueUbicacion extends ConsumerWidget {
  const _BloqueUbicacion();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final estado = ref.watch(ubicacionProvider);
    final colores = Theme.of(context).colorScheme;

    return Container(
      key: const Key('bloque_ubicacion'),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: colores.outlineVariant),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.place_outlined, size: 20),
              const SizedBox(width: 8),
              const Expanded(child: Text('Ubicación')),
              if (estado.leyendo)
                const SizedBox(
                  height: 16,
                  width: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              else
                TextButton.icon(
                  key: const Key('boton_releer_gps'),
                  onPressed: () => ref.read(ubicacionProvider.notifier).leer(),
                  icon: const Icon(Icons.my_location, size: 18),
                  label: const Text('Leer GPS'),
                ),
            ],
          ),
          const SizedBox(height: 4),
          _Diagnostico(estado: estado),
          if (estado.ubicacion != null) ...[
            const SizedBox(height: 12),
            const _AjusteCardinal(),
          ],
        ],
      ),
    );
  }
}

/// Qué pasó con el GPS, en lenguaje que sirva para actuar.
class _Diagnostico extends StatelessWidget {
  const _Diagnostico({required this.estado});

  final EstadoUbicacion estado;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final ubicacion = estado.ubicacion;

    if (estado.leyendo && ubicacion == null) {
      return const Text('Buscando señal del satélite…', key: Key('gps_buscando'));
    }

    final aviso = switch (estado.lectura) {
      GpsSinPermiso(definitivo: true) => (
          'Sin permiso de ubicación',
          'Actívalo en los ajustes del teléfono. Puedes guardar sin coordenadas.',
        ),
      GpsSinPermiso() => (
          'Sin permiso de ubicación',
          'Puedes guardar sin coordenadas.',
        ),
      GpsApagado() => (
          'La ubicación está apagada',
          'Enciéndela desde la barra de notificaciones.',
        ),
      GpsSinLectura() when ubicacion == null => (
          'No hay señal de GPS aquí',
          'Normal bajo techo. Puedes guardar sin coordenadas o ponerlas a mano.',
        ),
      _ => null,
    };

    if (aviso != null) {
      return Container(
        key: const Key('gps_aviso'),
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: colores.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(aviso.$1, style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(aviso.$2, style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      );
    }

    if (ubicacion == null) return const SizedBox.shrink();

    final (etiqueta, color) = switch (ubicacion.calidad) {
      CalidadGps.buena => ('Buena señal', colores.primary),
      CalidadGps.aceptable => ('Señal aceptable', colores.primary),
      CalidadGps.mala => ('Señal débil: conviene ajustar', colores.error),
      CalidadGps.sinLectura => ('Puesta a mano', colores.onSurfaceVariant),
    };

    final precision = ubicacion.precisionMetros;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          key: const Key('gps_coordenadas'),
          '${ubicacion.latTexto}, ${ubicacion.lngTexto}',
          style: const TextStyle(fontFamily: 'monospace'),
        ),
        const SizedBox(height: 2),
        Text(
          key: const Key('gps_calidad'),
          precision == null ? etiqueta : '$etiqueta · ±${precision.round()} m',
          style: TextStyle(color: color, fontSize: 12),
        ),
        if (estado.metrosMovidos >= 1)
          Text(
            key: const Key('gps_movido'),
            'Movido ${estado.metrosMovidos.round()} m del punto original',
            style: Theme.of(context).textTheme.bodySmall,
          ),
      ],
    );
  }
}

/// Corrección por desplazamientos cardinales. Funciona sin red.
class _AjusteCardinal extends ConsumerStatefulWidget {
  const _AjusteCardinal();

  @override
  ConsumerState<_AjusteCardinal> createState() => _EstadoAjuste();
}

class _EstadoAjuste extends ConsumerState<_AjusteCardinal> {
  double _paso = 10;

  @override
  Widget build(BuildContext context) {
    final control = ref.read(ubicacionProvider.notifier);
    final movido = ref.watch(ubicacionProvider).metrosMovidos;

    Widget flecha(String clave, IconData icono, {double norte = 0, double este = 0}) =>
        IconButton.outlined(
          key: Key(clave),
          icon: Icon(icono),
          onPressed: () => control.mover(norte: norte * _paso, este: este * _paso),
        );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // La etiqueta cede espacio antes que el selector: en un teléfono
        // angosto el control tiene que seguir siendo usable, y el texto puede
        // recortarse sin que se pierda nada.
        Row(
          children: [
            const Expanded(
              child: Text(
                'Ajustar a mano',
                style: TextStyle(fontSize: 13),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: 8),
            SegmentedButton<double>(
              key: const Key('selector_paso'),
              showSelectedIcon: false,
              style: const ButtonStyle(visualDensity: VisualDensity.compact),
              segments: const [
                ButtonSegment(value: 10, label: Text('10 m')),
                ButtonSegment(value: 50, label: Text('50 m')),
              ],
              selected: {_paso},
              onSelectionChanged: (s) => setState(() => _paso = s.first),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Center(
          child: Column(
            children: [
              flecha('mover_norte', Icons.keyboard_arrow_up, norte: 1),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  flecha('mover_oeste', Icons.keyboard_arrow_left, este: -1),
                  const SizedBox(width: 44),
                  flecha('mover_este', Icons.keyboard_arrow_right, este: 1),
                ],
              ),
              flecha('mover_sur', Icons.keyboard_arrow_down, norte: -1),
            ],
          ),
        ),
        if (movido >= 1)
          Center(
            child: TextButton(
              key: const Key('boton_deshacer_ajuste'),
              onPressed: control.deshacerCorreccion,
              child: const Text('Volver al punto del GPS'),
            ),
          ),
      ],
    );
  }
}

/// Aviso de posible duplicado, antes de crearlo.
class _AvisoCercanos extends StatelessWidget {
  const _AvisoCercanos({
    required this.cercanos,
    required this.confirmado,
    required this.alConfirmar,
  });

  final List<PosibleDuplicado> cercanos;
  final bool confirmado;
  final VoidCallback alConfirmar;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    return Container(
      key: const Key('aviso_cercanos'),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: confirmado ? colores.surfaceContainerHighest : colores.tertiaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            confirmado
                ? 'Confirmaste que es un negocio distinto'
                : 'Ya hay ${cercanos.length == 1 ? "un cliente" : "clientes"} aquí cerca',
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 6),
          for (final c in cercanos.take(4))
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Text('· ${c.nombreComercial} — a ${c.distanciaMetros.round()} m'),
            ),
          if (!confirmado) ...[
            const SizedBox(height: 8),
            Text(
              'Si es uno de ellos, cancela y búscalo en tu ruta.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 4),
            FilledButton.tonal(
              key: const Key('boton_es_nueva'),
              onPressed: alConfirmar,
              child: const Text('Es un negocio distinto'),
            ),
          ],
        ],
      ),
    );
  }
}
