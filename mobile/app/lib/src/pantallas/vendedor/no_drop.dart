/// La visita que no terminó en venta.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE MIDE, Y POR QUÉ NO ES PAPELEO
/// ─────────────────────────────────────────────────────────────────────────
/// Sin no-drops, un día de 20 visitas con 12 ventas se ve igual que un día de 12
/// visitas con 12 ventas, y **desde la oficina son indistinguibles**. El primero
/// tiene ocho clientes que necesitan algo; el segundo, un vendedor que se fue
/// temprano.
///
/// Con ellos se puede preguntar lo que de verdad importa: cuántas visitas
/// perdidas son culpa nuestra —no traía lo que pidió, se le acabó el crédito— y
/// cuántas del cliente.
///
/// ─────────────────────────────────────────────────────────────────────────
/// SIN GPS TAMBIÉN SE REGISTRA (ADR 0002 §158)
/// ─────────────────────────────────────────────────────────────────────────
/// Hasta la +51 era el único documento que no se podía guardar sin ubicación:
/// sin coordenadas, "estuve ahí y no compró" no se distingue de "no fui". Pero
/// el vendedor trabaja sin señal y muchas veces bajo techo, donde el GPS no lee,
/// y con el botón apagado no podía cerrar al cliente: la tienda se le quedaba
/// pendiente en la lista.
///
/// Ahora la pantalla pide el GPS al abrir y, si llega, la visita va con él; si
/// no, se registra igual y la oficina la ve marcada «Sin GPS». Sigue explicando
/// en lenguaje accionable qué falló —permiso, GPS apagado, satélite sin
/// respuesta— por si el vendedor lo puede arreglar en el momento.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL MOTIVO ES CATÁLOGO CERRADO
/// ─────────────────────────────────────────────────────────────────────────
/// "cerrado", "estaba cerrado", "cerrado!!" y "crrado" son cuatro categorías
/// distintas para cualquier reporte. El catálogo viene del servidor en el orden
/// que definió la oficina: en la calle, con el cliente esperando, un catálogo
/// alfabético obliga a leer diez opciones para encontrar "cerrado".
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_clientes.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart';
import '../../estado/mermas.dart';
import '../../estado/sesion.dart';
import 'comunes.dart';

class PantallaNoDrop extends ConsumerStatefulWidget {
  const PantallaNoDrop({super.key, required this.cliente});

  final ClienteEnRuta cliente;

  @override
  ConsumerState<PantallaNoDrop> createState() => _EstadoNoDropPantalla();
}

class _EstadoNoDropPantalla extends ConsumerState<PantallaNoDrop> {
  final _nota = TextEditingController();
  String? _motivo;
  String? _error;

  /// «Ven mañana»: el día al que el cliente pidió que se volviera, `YYYY-MM-DD`
  /// (ADR 0002 §103). Nulo si no pidió nada. Mueve ESTA visita; sus días fijos
  /// no cambian.
  String? _reprogramada;

  @override
  void initState() {
    super.initState();
    // Se pide al abrir: el satélite tarda, y pedirlo al tocar "registrar" dejaría
    // al vendedor esperando con el cliente enfrente. Antes se olvida la lectura
    // de la tienda anterior: si el GPS no lee aquí, esta visita va sin GPS, no
    // con el punto de otra.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final gps = ref.read(ubicacionProvider.notifier)..empezarDeCero();
      gps.leer();
    });
  }

  @override
  void dispose() {
    _nota.dispose();
    super.dispose();
  }

  void _registrar(List<MotivoDeNoDrop> motivos) {
    if (_motivo == null) {
      setState(() => _error = 'Escoge por qué no te compró.');
      return;
    }
    final motivo = motivos.where((m) => m.codigo == _motivo).firstOrNull;
    if (motivo != null && motivo.requiereNota && _nota.text.trim().isEmpty) {
      setState(
        () => _error = 'Este motivo necesita que escribas qué pasó.',
      );
      return;
    }

    setState(() => _error = null);
    ref.read(noDropProvider.notifier).registrar(
          clienteId: widget.cliente.id,
          motivoCodigo: _motivo!,
          ubicacion: ref.read(ubicacionProvider).ubicacion,
          nota: _nota.text,
          reprogramadaPara: _reprogramada,
        );
  }

  /// Hoy, como lo cuenta el teléfono para los documentos.
  DateTime get _hoy {
    // El día LOCAL, como `diaOperativoDe`: el reloj puede venir en UTC.
    final ahora = ref.read(relojProvider)().toLocal();
    return DateTime(ahora.year, ahora.month, ahora.day);
  }

  void _reprogramarEn(int dias) => setState(
        () => _reprogramada = diaOperativoDe(_hoy.add(Duration(days: dias))),
      );

  Future<void> _escogerDia() async {
    final hoy = _hoy;
    final dia = await showDatePicker(
      context: context,
      initialDate: _reprogramada != null
          ? DateTime.parse(_reprogramada!)
          : hoy.add(const Duration(days: 1)),
      firstDate: hoy.add(const Duration(days: 1)),
      lastDate: hoy.add(const Duration(days: diasMaximosParaReprogramar)),
      helpText: '¿Qué día te pidió que volvieras?',
    );
    if (dia == null || !mounted) return;
    setState(() => _reprogramada = diaOperativoDe(dia));
  }

  Widget _reprogramar() {
    final manana = diaOperativoDe(_hoy.add(const Duration(days: 1)));
    final pasado = diaOperativoDe(_hoy.add(const Duration(days: 2)));
    final otro = _reprogramada != null && _reprogramada != manana && _reprogramada != pasado;
    return Column(
      key: const Key('bloque_reprogramar'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('¿Te pidió que volvieras otro día?'),
        const SizedBox(height: 6),
        Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            ChoiceChip(
              key: const Key('reprogramar_no'),
              label: const Text('No'),
              selected: _reprogramada == null,
              onSelected: (_) => setState(() => _reprogramada = null),
            ),
            ChoiceChip(
              key: const Key('reprogramar_manana'),
              label: const Text('Mañana'),
              selected: _reprogramada == manana,
              onSelected: (_) => _reprogramarEn(1),
            ),
            ChoiceChip(
              key: const Key('reprogramar_pasado'),
              label: const Text('Pasado mañana'),
              selected: _reprogramada == pasado,
              onSelected: (_) => _reprogramarEn(2),
            ),
            ChoiceChip(
              key: const Key('reprogramar_otro_dia'),
              avatar: const Icon(Icons.event, size: 18),
              label: Text(otro ? diaEnPalabras(_reprogramada!) : 'Otro día…'),
              selected: otro,
              onSelected: (_) => _escogerDia(),
            ),
          ],
        ),
        if (_reprogramada != null)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              'Te sale en «Hoy» el ${diaEnPalabras(_reprogramada!)}. Sus días de '
              'siempre no cambian.',
              key: const Key('aviso_reprogramada'),
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final estado = ref.watch(noDropProvider);
    final motivos = ref.watch(motivosDeNoDropProvider);
    final folios = ref.watch(foliosDeNoDropProvider);
    final ubicacion = ref.watch(ubicacionProvider);

    if (estado is NoDropRegistrado) {
      return _NoDropGuardadoVista(
        noDrop: estado.noDrop,
        cliente: widget.cliente,
        alTerminar: () {
          ref.read(noDropProvider.notifier).reiniciar();
          Navigator.of(context).pop();
        },
      );
    }

    if (motivos.isEmpty) {
      return Scaffold(
        appBar: AppBar(title: const Text('No me compró')),
        body: const Padding(
          padding: EdgeInsets.all(24),
          child: Aviso(
            'Este equipo todavía no recibió el catálogo de motivos. Sincroniza '
            'con señal y vuelve a entrar.',
            grave: true,
          ),
        ),
      );
    }

    final elegido = motivos.where((m) => m.codigo == _motivo).firstOrNull;
    final tieneUbicacion = ubicacion.ubicacion != null;

    return Scaffold(
      appBar: AppBar(title: const Text('No me compró')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            widget.cliente.nombreComercial,
            style: Theme.of(context).textTheme.titleLarge,
          ),
          if (widget.cliente.codigo != null)
            Text(
              widget.cliente.codigo!,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          const SizedBox(height: 20),

          const Text('Por qué no te compró'),
          const SizedBox(height: 6),
          DropdownButtonFormField<String>(
            key: const Key('motivo_de_no_drop'),
            initialValue: _motivo,
            isExpanded: true,
            decoration: const InputDecoration(border: OutlineInputBorder()),
            hint: const Text('Escoge el motivo'),
            items: [
              for (final m in motivos)
                DropdownMenuItem(value: m.codigo, child: Text(m.nombre)),
            ],
            onChanged: (v) => setState(() => _motivo = v),
          ),
          if (elegido != null && elegido.esNuestraCulpa) ...[
            const SizedBox(height: 8),
            const Aviso(
              'Esto lo podemos arreglar nosotros. La oficina lo ve agrupado y '
              'es lo que hace que cambie.',
            ),
          ],
          const SizedBox(height: 16),

          TextField(
            key: const Key('nota_no_drop'),
            controller: _nota,
            maxLines: 2,
            decoration: InputDecoration(
              labelText: elegido != null && elegido.requiereNota
                  ? 'Qué pasó'
                  : 'Qué pasó (opcional)',
              helperText: elegido != null && elegido.requiereNota
                  ? 'Este motivo no dice nada sin explicación.'
                  : null,
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 16),

          // «Ven mañana» (ADR 0002 §103).
          _reprogramar(),
          const SizedBox(height: 20),

          _BloqueGps(estado: ubicacion),

          if (folios != null && folios.porAgotarse) ...[
            const SizedBox(height: 16),
            Aviso(
              'Te quedan ${folios.restantes} folios de visita. Sincroniza cuando '
              'tengas señal para pedir más.',
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 16),
            Aviso(_error!, grave: true),
          ],
          if (estado is NoDropFallido) ...[
            const SizedBox(height: 16),
            Aviso(estado.mensaje, grave: true),
          ],
          if (estado is NoDropSinIdentidad) ...[
            const SizedBox(height: 16),
            const Aviso(
              'Este equipo no tiene credencial o no está registrado. Vuelve a '
              'entrar con señal.',
              grave: true,
            ),
          ],

          const SizedBox(height: 24),
          FilledButton(
            key: const Key('registrar_no_drop'),
            // Con GPS o sin él (ADR 0002 §158): el vendedor tiene que poder
            // cerrar al cliente aunque el satélite no lea.
            onPressed: estado is NoDropEnCurso ? null : () => _registrar(motivos),
            child: estado is NoDropEnCurso
                ? const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Registrar la visita'),
          ),
          if (!tieneUbicacion) ...[
            const SizedBox(height: 8),
            Text(
              ubicacion.leyendo
                  ? 'Si el GPS no llega, regístrala igual: se guarda sin '
                      'ubicación y la oficina la ve marcada «Sin GPS».'
                  : 'Sin GPS también se guarda: la oficina la ve marcada '
                      '«Sin GPS».',
              key: const Key('aviso_sin_gps_no_drop'),
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}

/// Qué pasó con el GPS, en lenguaje que sirva para actuar.
///
/// Cada falla se resuelve distinto: el permiso se da en los ajustes, el GPS
/// apagado se prende, y el satélite que no respondió se arregla saliendo del
/// techado. Un "no se pudo obtener la ubicación" no lleva a ninguna de las tres.
class _BloqueGps extends ConsumerWidget {
  const _BloqueGps({required this.estado});

  final EstadoUbicacion estado;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;

    final (icono, texto) = switch (estado) {
      EstadoUbicacion(leyendo: true) => (
          Icons.my_location,
          'Buscando el satélite…',
        ),
      EstadoUbicacion(ubicacion: final u?) => (
          Icons.place,
          'Ubicación lista (±${u.precisionMetros?.round() ?? '?'} m)',
        ),
      EstadoUbicacion(lectura: GpsSinPermiso(definitivo: true)) => (
          Icons.lock_outline,
          'La aplicación no tiene permiso de ubicación. Dáselo en los ajustes '
              'del teléfono.',
        ),
      EstadoUbicacion(lectura: GpsSinPermiso()) => (
          Icons.lock_outline,
          'Hace falta el permiso de ubicación. Vuelve a intentar y acéptalo.',
        ),
      EstadoUbicacion(lectura: GpsApagado()) => (
          Icons.gps_off,
          'El GPS del teléfono está apagado. Préndelo y vuelve a intentar.',
        ),
      EstadoUbicacion(lectura: GpsSinLectura(:final detalle)) => (
          Icons.satellite_alt_outlined,
          'No llegó la señal${detalle == null ? '' : ': $detalle'}. Si estás '
              'bajo techo, sal un momento a la calle.',
        ),
      _ => (Icons.place_outlined, 'Todavía no se ha leído la ubicación.'),
    };

    return Container(
      key: const Key('bloque_gps_no_drop'),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: colores.outlineVariant),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(icono, size: 20),
          const SizedBox(width: 10),
          Expanded(child: Text(texto)),
          if (estado.leyendo)
            const SizedBox(
              height: 16,
              width: 16,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          else
            TextButton(
              key: const Key('releer_gps_no_drop'),
              onPressed: () => ref.read(ubicacionProvider.notifier).leer(),
              child: const Text('Leer GPS'),
            ),
        ],
      ),
    );
  }
}

class _NoDropGuardadoVista extends StatelessWidget {
  const _NoDropGuardadoVista({
    required this.noDrop,
    required this.cliente,
    required this.alTerminar,
  });

  final NoDropGuardado noDrop;
  final ClienteEnRuta cliente;
  final VoidCallback alTerminar;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Text('Visita registrada'),
          automaticallyImplyLeading: false,
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Center(
              child: Column(
                children: [
                  const Icon(Icons.check_circle, size: 56),
                  const SizedBox(height: 8),
                  Text(
                    cliente.nombreComercial,
                    style: Theme.of(context).textTheme.titleLarge,
                    textAlign: TextAlign.center,
                  ),
                  Text(
                    'Folio ${noDrop.folioConsecutivo}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            Text(
              'Queda constancia de que pasaste. Sin esto, el día se vería como '
              'si nunca hubieras venido.',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            if (noDrop.ubicacion == null) ...[
              const SizedBox(height: 12),
              Text(
                'Se guardó sin GPS: la oficina la ve marcada «Sin GPS».',
                key: const Key('guardado_sin_gps'),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            if (noDrop.reprogramadaPara != null) ...[
              const SizedBox(height: 12),
              Text(
                'Visita reprogramada para el ${diaEnPalabras(noDrop.reprogramadaPara!)}: '
                'ese día te sale en «Hoy».',
                key: const Key('guardado_reprogramada'),
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ],
            const SizedBox(height: 24),
            FilledButton(
              key: const Key('no_drop_listo'),
              onPressed: alTerminar,
              child: const Text('Listo'),
            ),
          ],
        ),
      );
}
