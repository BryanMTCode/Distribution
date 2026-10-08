/// El cierre del día del vendedor: «Corte del día» y «Solicitar carga».
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL FLUJO (ADR 0002 §82)
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **Corte del día.** Cuenta lo que le sobró arriba del camión y el efectivo
///    que entrega. Sale el ticket con lo vendido, el efectivo y el sobrante.
/// 2. **Solicitar carga.** Ahí mismo pide la carga de mañana. Solo hay una al
///    día y es para el día siguiente.
/// 3. La oficina la acepta y le llega con la sincronización: «aceptada», con el
///    folio, y la mercancía arriba del camión.
///
/// Todo funciona sin señal: el corte y la solicitud se guardan aquí y viajan
/// por la cola. Los tickets se comparten como texto (WhatsApp).
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL CONTEO ES A CIEGAS
/// ─────────────────────────────────────────────────────────────────────────
/// La pantalla no dice cuánto cree el sistema que trae de cada producto. Con
/// la cifra a la vista el conteo se vuelve un copiado, y el faltante se descubre
/// el día que alguien cuenta de verdad. La comparación la ve la oficina antes
/// de aceptar.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/cierre.dart';
import '../../estado/sesion.dart';
import '../../estado/sincronizacion.dart';
import '../ticket_compartible.dart';
import 'comunes.dart';

/// «2,200.50» → `Dinero`. Nulo si no se entiende; el vacío también.
Dinero? leerPesos(String texto) {
  final limpio = texto.trim().replaceAll(',', '').replaceAll(r'$', '');
  final m = RegExp(r'^(\d{1,9})(?:\.(\d{1,2}))?$').firstMatch(limpio);
  if (m == null) return null;
  return Dinero.deTexto('${m.group(1)}.${(m.group(2) ?? '').padRight(2, '0')}');
}

// ===========================================================================
// Corte del día
// ===========================================================================

class PantallaCorteDelDia extends ConsumerStatefulWidget {
  const PantallaCorteDelDia({super.key});

  @override
  ConsumerState<PantallaCorteDelDia> createState() => _EstadoCorte();
}

class _EstadoCorte extends ConsumerState<PantallaCorteDelDia> {
  final _efectivo = TextEditingController();
  final _observaciones = TextEditingController();
  final Map<String, TextEditingController> _conteo = {};
  String? _error;

  @override
  void dispose() {
    _efectivo.dispose();
    _observaciones.dispose();
    for (final c in _conteo.values) {
      c.dispose();
    }
    super.dispose();
  }

  TextEditingController _campo(String productoId) =>
      _conteo.putIfAbsent(productoId, TextEditingController.new);

  void _terminar(ResumenDelDia resumen) {
    final registro = ref.read(registroDeCierreProvider);
    if (registro == null) return;

    final conteo = <String, Cantidad>{};
    final faltan = <String>[];
    for (final p in resumen.productos) {
      final texto = _campo(p.productoId).text.trim();
      if (texto.isEmpty) {
        faltan.add(p.nombre);
        continue;
      }
      conteo[p.productoId] = Cantidad.deEnteros(int.parse(texto));
    }
    if (faltan.isNotEmpty) {
      setState(() => _error = 'Falta contar: ${faltan.take(3).join(', ')}'
          '${faltan.length > 3 ? ' y ${faltan.length - 3} más' : ''}. '
          'Si no te quedó nada, escribe 0.');
      return;
    }
    final efectivo = leerPesos(_efectivo.text);
    if (efectivo == null) {
      setState(() => _error = 'Escribe cuánto efectivo entregas, por ejemplo 2250.50.');
      return;
    }

    final corte = registro.hacerCorte(
      conteo: conteo,
      efectivo: efectivo,
      observaciones: _observaciones.text,
    );
    ref.read(revisionDelCierreProvider.notifier).state++;
    ref.invalidate(resumenColaProvider);
    // El navegador y no el `context`: esta pantalla deja de existir al
    // reemplazarse por el ticket, y el botón de seguir vive en el ticket.
    final navegador = Navigator.of(context);
    navegador.pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => PantallaTicketCompartible(
          titulo: 'Corte del día',
          texto: corte.textoTicket,
          aviso: 'Tu corte quedó guardado. Se manda a la oficina al sincronizar, '
              'detrás de todas tus ventas de hoy.',
          etiquetaSiguiente: 'Solicitar carga para mañana',
          iconoSiguiente: Icons.local_shipping_outlined,
          siguiente: () => navegador.pushReplacement(
            MaterialPageRoute<void>(builder: (_) => const PantallaSolicitarCarga()),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final registro = ref.watch(registroDeCierreProvider);
    if (registro == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Corte del día')),
        body: const Padding(
          padding: EdgeInsets.all(16),
          child: Aviso('Este equipo todavía no está registrado. Sincroniza primero.',
              grave: true),
        ),
      );
    }
    final resumen = registro.resumen();
    final estilo = Theme.of(context).textTheme;

    return Scaffold(
      key: const Key('pantalla_corte_del_dia'),
      appBar: AppBar(title: const Text('Corte del día')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Vendiste ${resumen.vendido.enPesos}', style: estilo.titleLarge),
                  Text('${resumen.ventas} venta(s) hoy'),
                  const SizedBox(height: 8),
                  Text(
                    'En efectivo: ${resumen.efectivo.enPesos}',
                    key: const Key('corte_efectivo_vendido'),
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  Text('Por transferencia: ${resumen.transferencias.enPesos} '
                      '(llegó al banco, no a tu bolsa)'),
                ],
              ),
            ),
          ),
          if (resumen.sinSincronizar > 0)
            Aviso(
              '${resumen.sinSincronizar} venta(s) no han subido. El corte viaja '
              'detrás de ellas; sincroniza en cuanto tengas señal.',
            ),
          const SizedBox(height: 16),
          Text('¿Cuánto efectivo entregas?', style: estilo.titleMedium),
          const SizedBox(height: 8),
          TextField(
            key: const Key('campo_efectivo_corte'),
            controller: _efectivo,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              prefixText: r'$ ',
              border: OutlineInputBorder(),
              hintText: 'Lo que traes en la bolsa',
            ),
          ),
          const SizedBox(height: 20),
          Text('Cuenta lo que te sobró en el camión', style: estilo.titleMedium),
          const Text('En piezas. Si de algo no te quedó nada, escribe 0.'),
          const SizedBox(height: 8),
          if (resumen.productos.isEmpty)
            const Aviso('Tu camión no tiene productos registrados: no hay nada que contar.'),
          for (final p in resumen.productos)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(p.nombre),
                        Text(
                          [
                            p.sku,
                            if (p.mayor != null)
                              '${p.mayor!.unidad} de ${p.mayor!.factor.textoCorto}',
                          ].join(' · '),
                          style: estilo.bodySmall,
                        ),
                      ],
                    ),
                  ),
                  SizedBox(
                    width: 96,
                    child: TextField(
                      key: Key('conteo_${p.sku}'),
                      controller: _campo(p.productoId),
                      keyboardType: TextInputType.number,
                      inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                      textAlign: TextAlign.end,
                      decoration: InputDecoration(
                        isDense: true,
                        border: const OutlineInputBorder(),
                        suffixText: p.unidadBase,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('campo_observaciones_corte'),
            controller: _observaciones,
            maxLength: 300,
            decoration: const InputDecoration(
              labelText: 'Notas para la oficina (opcional)',
              border: OutlineInputBorder(),
            ),
          ),
          if (_error != null) Aviso(_error!, grave: true),
          const SizedBox(height: 8),
          FilledButton.icon(
            key: const Key('boton_terminar_corte'),
            onPressed: () => _terminar(resumen),
            icon: const Icon(Icons.check),
            label: const Text('Terminar el corte'),
          ),
        ],
      ),
    );
  }
}

// ===========================================================================
// Solicitar carga
// ===========================================================================

class PantallaSolicitarCarga extends ConsumerStatefulWidget {
  const PantallaSolicitarCarga({super.key});

  @override
  ConsumerState<PantallaSolicitarCarga> createState() => _EstadoSolicitud();
}

class _EnPedido {
  _EnPedido(this.producto) : presentacion = producto.porOmision;

  final ProductoParaPedir producto;
  Presentacion presentacion;
  final bultos = TextEditingController();
}

class _EstadoSolicitud extends ConsumerState<PantallaSolicitarCarga> {
  final _busqueda = TextEditingController();
  final _observaciones = TextEditingController();
  final Map<String, _EnPedido> _pedidos = {};
  String? _error;

  @override
  void dispose() {
    _busqueda.dispose();
    _observaciones.dispose();
    for (final p in _pedidos.values) {
      p.bultos.dispose();
    }
    super.dispose();
  }

  _EnPedido _pedido(ProductoParaPedir p) => _pedidos.putIfAbsent(p.productoId, () => _EnPedido(p));

  void _enviar() {
    final registro = ref.read(registroDeCierreProvider);
    if (registro == null) return;
    final renglones = [
      for (final p in _pedidos.values)
        if ((int.tryParse(p.bultos.text.trim()) ?? 0) > 0)
          RenglonPedido(
            productoId: p.producto.productoId,
            nombre: p.producto.nombre,
            unidad: p.presentacion.unidad,
            factor: p.presentacion.factor,
            bultos: int.parse(p.bultos.text.trim()),
            unidadBase: p.producto.unidadBase,
          ),
    ];
    if (renglones.isEmpty) {
      setState(() => _error = 'Escribe cuántas cajas quieres de al menos un producto.');
      return;
    }
    final solicitud = registro.pedirCarga(
      renglones: renglones,
      observaciones: _observaciones.text,
    );
    ref.read(revisionDelCierreProvider.notifier).state++;
    ref.invalidate(resumenColaProvider);
    final sync = ref.read(syncProvider.notifier);
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => PantallaTicketCompartible(
          titulo: 'Solicitud de carga',
          texto: registro.ticketDe(solicitud),
          aviso: 'Tu solicitud quedó guardada. Se manda a la oficina al '
              'sincronizar; cuando la acepten, aquí mismo vas a ver el folio de '
              'tu carga.',
          etiquetaSiguiente: 'Sincronizar ahora',
          iconoSiguiente: Icons.sync,
          siguiente: sync.sincronizar,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final registro = ref.watch(registroDeCierreProvider);
    final estilo = Theme.of(context).textTheme;
    final productos = registro?.catalogoParaPedir(busqueda: _busqueda.text) ?? const [];
    final manana = registro == null ? null : diaSiguiente(registro.hoy);
    final pedidos = _pedidos.values
        .where((p) => (int.tryParse(p.bultos.text.trim()) ?? 0) > 0)
        .length;

    return Scaffold(
      key: const Key('pantalla_solicitar_carga'),
      appBar: AppBar(title: const Text('Solicitar carga')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (manana != null)
            Text('Para el ${diaEnPalabras(manana)}', style: estilo.titleMedium),
          const Text('Una carga al día, y es para mañana. La oficina la revisa y te '
              'avisa cuando la acepte.'),
          const SizedBox(height: 12),
          TextField(
            key: const Key('buscar_producto_carga'),
            controller: _busqueda,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              hintText: 'Buscar producto',
              prefixIcon: Icon(Icons.search),
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          for (final p in productos)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(p.nombre),
                        Text(p.sku, style: estilo.bodySmall),
                      ],
                    ),
                  ),
                  if (p.presentaciones.length > 1)
                    DropdownButton<String>(
                      key: Key('presentacion_${p.sku}'),
                      value: _pedido(p).presentacion.unidad,
                      items: [
                        for (final u in p.presentaciones)
                          DropdownMenuItem(
                            value: u.unidad,
                            child: Text(u.factor.esUno
                                ? u.unidad
                                : '${u.unidad} ${u.factor.textoCorto}'),
                          ),
                      ],
                      onChanged: (u) => setState(() => _pedido(p).presentacion =
                          p.presentaciones.firstWhere((x) => x.unidad == u)),
                    )
                  else
                    Text(p.porOmision.unidad),
                  const SizedBox(width: 8),
                  SizedBox(
                    width: 72,
                    child: TextField(
                      key: Key('pedido_${p.sku}'),
                      controller: _pedido(p).bultos,
                      keyboardType: TextInputType.number,
                      inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                      textAlign: TextAlign.end,
                      onChanged: (_) => setState(() {}),
                      decoration: const InputDecoration(
                        isDense: true,
                        border: OutlineInputBorder(),
                        hintText: '0',
                      ),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('campo_observaciones_solicitud'),
            controller: _observaciones,
            maxLength: 300,
            decoration: const InputDecoration(
              labelText: 'Notas para la oficina (opcional)',
              border: OutlineInputBorder(),
            ),
          ),
          if (_error != null) Aviso(_error!, grave: true),
          const SizedBox(height: 8),
          FilledButton.icon(
            key: const Key('boton_enviar_solicitud'),
            onPressed: _enviar,
            icon: const Icon(Icons.send_outlined),
            label: Text(pedidos == 0
                ? 'Enviar solicitud'
                : 'Enviar solicitud ($pedidos producto${pedidos == 1 ? '' : 's'})'),
          ),
        ],
      ),
    );
  }
}

// ===========================================================================
// La tarjeta de «Mi día»
// ===========================================================================

/// El cierre de hoy en «Mi día»: hacer el corte, pedir la carga, ver sus tickets
/// y qué contestó la oficina.
class TarjetaDelCierre extends ConsumerWidget {
  const TarjetaDelCierre({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final registro = ref.watch(registroDeCierreProvider);
    if (registro == null) return const SizedBox.shrink();
    final cierre = ref.watch(cierreDeHoyProvider);
    final corte = cierre.corte;
    final solicitud = cierre.solicitud;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;

    void abrir(Widget pantalla) =>
        Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => pantalla));

    return Card(
      key: const Key('tarjeta_cierre'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Cierre del día', style: estilo.titleMedium),
            const SizedBox(height: 8),
            if (corte == null) ...[
              const Text('Al terminar tu ruta: cuenta lo que te sobró y el efectivo, '
                  'y pide tu carga de mañana.'),
              const SizedBox(height: 8),
              FilledButton.icon(
                key: const Key('boton_corte_del_dia'),
                onPressed: () => abrir(const PantallaCorteDelDia()),
                icon: const Icon(Icons.fact_check_outlined),
                label: const Text('Hacer el corte del día'),
              ),
            ] else ...[
              Text(
                'Corte hecho a las ${_hora(corte.fechaDispositivo)}: entregas '
                '${corte.efectivoDeclarado.enPesos}'
                '${corte.sincronizado ? '' : ' · falta subirlo'}',
                key: const Key('estado_corte'),
              ),
              Wrap(
                spacing: 8,
                children: [
                  TextButton(
                    key: const Key('ver_ticket_corte'),
                    onPressed: () => abrir(PantallaTicketCompartible(
                      titulo: 'Corte del día',
                      texto: corte.textoTicket,
                    )),
                    child: const Text('Ver ticket'),
                  ),
                  TextButton(
                    key: const Key('rehacer_corte'),
                    onPressed: () => abrir(const PantallaCorteDelDia()),
                    child: const Text('Rehacer el corte'),
                  ),
                ],
              ),
            ],
            const Divider(height: 24),
            if (solicitud == null)
              OutlinedButton.icon(
                key: const Key('boton_solicitar_carga'),
                onPressed: () => abrir(const PantallaSolicitarCarga()),
                icon: const Icon(Icons.local_shipping_outlined),
                label: const Text('Solicitar carga para mañana'),
              )
            else ...[
              Text(
                switch (solicitud.estado) {
                  'aceptada' =>
                    'Carga de mañana ACEPTADA: ${solicitud.cargaFolio ?? ''}',
                  'rechazada' => 'La oficina rechazó tu solicitud: '
                      '${solicitud.motivo ?? 'sin motivo'}',
                  _ => solicitud.sincronizado
                      ? 'Carga de mañana pedida: la revisa la oficina.'
                      : 'Carga de mañana pedida: falta subirla.',
                },
                key: const Key('estado_solicitud'),
                style: TextStyle(
                  color: solicitud.rechazada ? colores.error : null,
                  fontWeight: solicitud.aceptada ? FontWeight.w600 : null,
                ),
              ),
              Wrap(
                spacing: 8,
                children: [
                  TextButton(
                    key: const Key('ver_ticket_carga'),
                    onPressed: () => abrir(PantallaTicketCompartible(
                      titulo: solicitud.aceptada ? 'Carga aceptada' : 'Solicitud de carga',
                      texto: registro.ticketDe(solicitud),
                    )),
                    child: const Text('Ver ticket'),
                  ),
                  if (!solicitud.aceptada)
                    TextButton(
                      key: const Key('corregir_solicitud'),
                      onPressed: () => abrir(const PantallaSolicitarCarga()),
                      child: Text(solicitud.rechazada ? 'Pedir otra' : 'Corregir'),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  static String _hora(String momento) {
    final l = DateTime.parse(momento).toLocal();
    return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
  }
}
