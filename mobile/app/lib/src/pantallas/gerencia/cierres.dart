/// Lo que mandan los vendedores al terminar el día, en la app del gerente.
///
/// El vendedor hace su corte y pide la carga de mañana desde su teléfono, sin
/// señal. El gerente los resuelve POR SEPARADO, cada uno en su pestaña (ADR
/// 0002 §82):
///
///   · **Corte del día** (`SeccionCortesDeVendedores`): el efectivo que entrega
///     contra lo que vendió en efectivo, y lo que le queda en el camión —lo que
///     traía, más la carga, menos lo vendido—. Nadie lo cuenta: cerrar el corte
///     deja el camión en ese cálculo, y lo único que puede ir a la cuenta del
///     vendedor es el efectivo que no entregó.
///   · **Cargas** (`SeccionCargasPedidas`): la carga pedida, producto por
///     producto, con lo que hay en la bodega. El gerente puede bajar o quitar un
///     renglón antes de aceptar. Con el corte del vendedor abierto, la carga
///     espera: primero se cierra el día.
///
/// Al aceptar sale el ticket de la carga para compartirlo. Solo hay una carga al
/// día, y es para el día siguiente.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import '../ticket_compartible.dart';
import 'comunes.dart';

ClienteCierres? _cliente(WidgetRef ref) {
  final t = ref.read(transporteProvider);
  return t == null ? null : ClienteCierres(t);
}

/// Por cerrar, lo que declaró el vendedor; cerrado, lo que recibió el gerente
/// (ADR 0002 §87), con lo declarado al lado si no coincidió.
String _efectivoEnPalabras(CorteRecibido c) {
  final d = c.diferenciaEfectivo;
  final recibido = c.efectivoRecibido;
  final base = recibido == null
      ? 'Declara ${pesos(c.efectivoDeclarado)} de ${pesos(c.efectivoEsperado)}'
      : 'Entregó ${pesos(recibido)} de ${pesos(c.efectivoEsperado)}'
          '${recibido == c.efectivoDeclarado ? '' : ' (declaró ${pesos(c.efectivoDeclarado)})'}';
  return '$base · ${_cuadre(d)}';
}

String _cuadre(Dinero d) {
  if (d.esCero) return 'cuadra';
  return d.esNegativo ? 'faltan ${pesos(-d)}' : 'sobran ${pesos(d)}';
}

/// «2,100.50» → `Dinero`. Nulo si no se entiende; el vacío también.
Dinero? _leerPesos(String texto) {
  final limpio = texto.trim().replaceAll(',', '').replaceAll(r'$', '');
  final m = RegExp(r'^(\d{1,9})(?:\.(\d{1,2}))?$').firstMatch(limpio);
  if (m == null) return null;
  return Dinero.deTexto('${m.group(1)}.${(m.group(2) ?? '').padRight(2, '0')}');
}

/// La lista de una de las dos secciones, que se carga sola.
abstract class _Seccion extends ConsumerStatefulWidget {
  const _Seccion({super.key, this.alCambiar});

  /// Para que la pantalla que la contiene se recargue: cerrar un corte cambia
  /// lo que queda por cortar a mano, y aceptar una carga, las cargas abiertas.
  final VoidCallback? alCambiar;
}

abstract class _EstadoSeccion<T extends _Seccion> extends ConsumerState<T> {
  Cierres? _cierres;
  String? _error;

  Future<Cierres> leer(ClienteCierres cliente);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => cargar());
  }

  Future<void> cargar() async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    try {
      final c = await leer(cliente);
      if (!mounted) return;
      setState(() {
        _cierres = c;
        _error = null;
      });
    } on ServidorSinEstaFuncion {
      // Un servidor anterior: la sección no aparece, sin alarmar a nadie.
      if (mounted) setState(() => _cierres = const Cierres(pendientes: [], recientes: []));
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> abrir(Widget pantalla) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => pantalla));
    await cargar();
    widget.alCambiar?.call();
  }
}

// ===========================================================================
// El corte
// ===========================================================================

/// Los cortes que mandaron los vendedores, para cerrarlos.
class SeccionCortesDeVendedores extends _Seccion {
  const SeccionCortesDeVendedores({super.key, super.alCambiar});

  @override
  ConsumerState<SeccionCortesDeVendedores> createState() => _EstadoCortesDeVendedores();
}

class _EstadoCortesDeVendedores extends _EstadoSeccion<SeccionCortesDeVendedores> {
  @override
  Future<Cierres> leer(ClienteCierres cliente) => cliente.cortes();

  @override
  Widget build(BuildContext context) {
    final c = _cierres;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return Column(
      key: const Key('seccion_cortes_vendedores'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Cortes de los vendedores', style: estilo.titleMedium),
        if (_error != null)
          Text('No se pudieron leer: $_error', style: TextStyle(color: colores.error)),
        if (c != null && c.pendientes.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text('Ningún corte por cerrar. Llegan cuando el teléfono del '
                'vendedor sincroniza.'),
          ),
        for (final cierre in c?.pendientes ?? const <CierreDeVendedor>[])
          Card(
            child: ListTile(
              key: Key('corte_vendedor_${cierre.vendedorCodigo}'),
              leading: const Icon(Icons.point_of_sale_outlined),
              title: Text(cierre.vendedor),
              subtitle: Text('Corte del ${diaEnPalabras(cierre.corte!.fechaOperativa)}\n'
                  '${_efectivoEnPalabras(cierre.corte!)}'),
              isThreeLine: true,
              trailing: const Icon(Icons.chevron_right),
              onTap: () => abrir(PantallaCorteDeVendedor(cierre: cierre)),
            ),
          ),
        if (c != null && c.recientes.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Cerrados esta semana', style: estilo.titleSmall),
          for (final r in c.recientes)
            ListTile(
              key: Key('corte_vendedor_reciente_${r.corte!.id}'),
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: Text('${r.vendedor} · ${diaEnPalabras(r.corte!.fechaOperativa)}'),
              subtitle: Text('${r.corte!.liquidacionFolio ?? r.corte!.estado} · '
                  '${_efectivoEnPalabras(r.corte!)}'),
            ),
        ],
      ],
    );
  }
}

/// Un corte del vendedor: lo que entrega, lo que le queda, y cerrarlo.
class PantallaCorteDeVendedor extends ConsumerStatefulWidget {
  const PantallaCorteDeVendedor({super.key, required this.cierre});

  final CierreDeVendedor cierre;

  @override
  ConsumerState<PantallaCorteDeVendedor> createState() => _EstadoCorteDeVendedor();
}

class _EstadoCorteDeVendedor extends ConsumerState<PantallaCorteDeVendedor> {
  // Arranca en lo que declaró el vendedor: si cuadra con lo que se cuenta, no
  // hay nada que escribir.
  late final _recibido = TextEditingController(
    text: widget.cierre.corte!.efectivoDeclarado.texto,
  );
  bool _ocupado = false;
  String? _error;

  @override
  void dispose() {
    _recibido.dispose();
    super.dispose();
  }

  Future<void> _cerrar() async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    final recibido = _leerPesos(_recibido.text);
    if (recibido == null) {
      setState(() => _error = 'Escribe el efectivo que recibes, por ejemplo 2250.50.');
      return;
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final hecho = await cliente.cerrarCorte(
        widget.cierre.corte!.id,
        efectivoRecibido: recibido,
      );
      if (!mounted) return;
      final mensajero = ScaffoldMessenger.of(context);
      Navigator.of(context).pop();
      mensajero.showSnackBar(SnackBar(
        content: Text(hecho.mensaje ?? 'Corte cerrado.'),
        duration: const Duration(seconds: 8),
      ));
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _ocupado = false;
        _error = explicarErrorDeOficina(e);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.cierre;
    final corte = c.corte!;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_corte_vendedor'),
      appBar: AppBar(title: Text(c.vendedor)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (c.camion != null) Text(c.camion!, style: estilo.titleSmall),
          Text('Corte del ${diaEnPalabras(corte.fechaOperativa)}', style: estilo.titleMedium),
          Text(
            _efectivoEnPalabras(corte),
            key: const Key('efectivo_del_corte'),
            style: TextStyle(
              color: corte.diferenciaEfectivo.esNegativo ? colores.error : null,
              fontWeight: FontWeight.w600,
            ),
          ),
          if ((corte.observaciones ?? '').isNotEmpty) Text('«${corte.observaciones}»'),
          if (corte.pendiente) ...[
            const SizedBox(height: 12),
            // Lo cuenta quien lo recibe (ADR 0002 §87): el arqueo y lo que se le
            // carga al vendedor salen de este número, no de lo que él declaró.
            TextField(
              key: const Key('campo_efectivo_recibido'),
              controller: _recibido,
              enabled: !_ocupado,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                labelText: 'Efectivo que recibes (cuéntalo)',
                prefixText: r'$ ',
                helperText: 'Él declaró ${pesos(corte.efectivoDeclarado)}',
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 6),
            Builder(builder: (context) {
              final recibido = _leerPesos(_recibido.text);
              if (recibido == null) return const SizedBox.shrink();
              final d = recibido - corte.efectivoEsperado;
              return Text(
                'Contra lo vendido en efectivo: ${_cuadre(d)}'
                '${d.esNegativo ? '. Lo que falta se carga a su cuenta.' : ''}',
                key: const Key('cuadre_recibido'),
                style: TextStyle(
                  color: d.esNegativo ? colores.error : null,
                  fontWeight: FontWeight.w600,
                ),
              );
            }),
          ],
          const Divider(height: 32),
          Text('Lo que le queda en el camión', style: estilo.titleMedium),
          const Text('Nadie lo cuenta: lo que traía, más la carga, menos lo vendido.'),
          const SizedBox(height: 8),
          if (corte.renglones.isEmpty) const Text('El camión está vacío.'),
          for (final r in corte.renglones)
            ListTile(
              key: Key('queda_${r.sku}'),
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: Text(r.nombre),
              subtitle: Text('Traía ${r.traia.textoCorto} · cargó ${r.cargada.textoCorto} · '
                  'vendió ${r.vendida.textoCorto}'
                  '${r.merma.esCero ? '' : ' · merma ${r.merma.textoCorto}'}'),
              trailing: Text(
                '${r.queda.textoCorto} ${r.unidadBase}',
                style: TextStyle(
                  fontWeight: FontWeight.w600,
                  color: r.queda.esNegativa ? colores.error : null,
                ),
              ),
            ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, key: const Key('error_corte_vendedor'),
                style: TextStyle(color: colores.error)),
          ],
          const SizedBox(height: 16),
          if (corte.pendiente)
            FilledButton.icon(
              key: const Key('boton_cerrar_corte_vendedor'),
              onPressed: _ocupado ? null : _cerrar,
              icon: const Icon(Icons.lock_outline),
              label: const Text('Cerrar el corte'),
            )
          else
            Text('Este corte ya está ${corte.estado}'
                '${corte.liquidacionFolio == null ? '' : ' (${corte.liquidacionFolio})'}.'),
        ],
      ),
    );
  }
}

// ===========================================================================
// La carga pedida
// ===========================================================================

/// Las cargas que pidieron los vendedores, para aceptarlas o rechazarlas.
class SeccionCargasPedidas extends _Seccion {
  const SeccionCargasPedidas({super.key, super.alCambiar});

  @override
  ConsumerState<SeccionCargasPedidas> createState() => _EstadoCargasPedidas();
}

class _EstadoCargasPedidas extends _EstadoSeccion<SeccionCargasPedidas> {
  @override
  Future<Cierres> leer(ClienteCierres cliente) => cliente.solicitudes();

  @override
  Widget build(BuildContext context) {
    final c = _cierres;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return Column(
      key: const Key('seccion_cargas_pedidas'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Cargas que pidieron los vendedores', style: estilo.titleMedium),
        if (_error != null)
          Text('No se pudieron leer: $_error', style: TextStyle(color: colores.error)),
        if (c != null && c.pendientes.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text('Ninguna carga pedida por aceptar.'),
          ),
        for (final cierre in c?.pendientes ?? const <CierreDeVendedor>[])
          Card(
            child: ListTile(
              key: Key('carga_pedida_${cierre.vendedorCodigo}'),
              leading: Icon(
                Icons.local_shipping_outlined,
                color: cierre.cortePorCerrar == null ? null : colores.error,
              ),
              title: Text(cierre.vendedor),
              subtitle: Text([
                'Para el ${diaEnPalabras(cierre.solicitud!.fechaOperativa)}: '
                    '${cierre.solicitud!.renglones.length} producto(s)',
                if (cierre.cortePorCerrar != null)
                  'Espera su corte del ${diaEnPalabras(cierre.cortePorCerrar!)}',
              ].join('\n')),
              isThreeLine: cierre.cortePorCerrar != null,
              trailing: const Icon(Icons.chevron_right),
              onTap: () => abrir(PantallaCargaPedida(cierre: cierre)),
            ),
          ),
        if (c != null && c.recientes.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Resueltas esta semana', style: estilo.titleSmall),
          for (final r in c.recientes)
            ListTile(
              key: Key('carga_pedida_reciente_${r.clave}'),
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: Text('${r.vendedor} · ${diaEnPalabras(r.solicitud!.fechaOperativa)}'),
              subtitle: Text(r.solicitud!.aceptada
                  ? 'Carga ${r.solicitud!.cargaFolio ?? ''}'
                  : 'Rechazada: ${r.solicitud!.motivo ?? ''}'),
              trailing: r.solicitud!.aceptada
                  ? IconButton(
                      key: Key('ticket_reciente_${r.clave}'),
                      tooltip: 'Ticket de la carga',
                      icon: const Icon(Icons.receipt_long_outlined),
                      onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(
                        builder: (_) => PantallaTicketCompartible(
                          titulo: 'Carga aceptada',
                          texto: r.ticketDeLaCarga!,
                        ),
                      )),
                    )
                  : null,
            ),
        ],
      ],
    );
  }
}

/// Una carga pedida: corregirla, aceptarla o rechazarla.
class PantallaCargaPedida extends ConsumerStatefulWidget {
  const PantallaCargaPedida({super.key, required this.cierre});

  final CierreDeVendedor cierre;

  @override
  ConsumerState<PantallaCargaPedida> createState() => _EstadoCargaPedida();
}

class _EstadoCargaPedida extends ConsumerState<PantallaCargaPedida> {
  final Map<String, TextEditingController> _bultos = {};
  bool _ocupado = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    for (final r in widget.cierre.solicitud!.renglones) {
      _bultos[r.productoId] = TextEditingController(text: r.bultos.textoCorto);
    }
  }

  @override
  void dispose() {
    for (final c in _bultos.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _aceptar() async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    final solicitud = widget.cierre.solicitud!;
    // Solo viaja lo que el gerente cambió: lo demás se acepta como se pidió.
    final cambios = <String, String>{};
    for (final r in solicitud.renglones) {
      final texto = _bultos[r.productoId]!.text.trim();
      final valor = texto.isEmpty ? '0' : texto;
      if (valor != r.bultos.textoCorto) cambios[r.productoId] = valor;
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final hecho = await cliente.aceptarSolicitud(solicitud.id, bultos: cambios);
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => PantallaTicketCompartible(
            titulo: 'Carga aceptada',
            texto: hecho.ticketDeLaCarga!,
            aviso: hecho.mensaje,
          ),
        ),
      );
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _ocupado = false;
        _error = explicarErrorDeOficina(e);
      });
    }
  }

  Future<void> _rechazar() async {
    final motivo = await showDialog<String>(
      context: context,
      builder: (_) => const _DialogoRechazo(),
    );
    if (motivo == null || !mounted) return;
    final cliente = _cliente(ref);
    if (cliente == null) return;
    setState(() => _ocupado = true);
    try {
      await cliente.rechazar(widget.cierre.solicitud!.id, motivo: motivo);
      if (!mounted) return;
      Navigator.of(context).pop();
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _ocupado = false;
        _error = explicarErrorDeOficina(e);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.cierre;
    final solicitud = c.solicitud!;
    final espera = c.cortePorCerrar;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;

    return Scaffold(
      key: const Key('pantalla_carga_pedida'),
      appBar: AppBar(title: Text(c.vendedor)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (c.camion != null) Text(c.camion!, style: estilo.titleSmall),
          Text('Carga para el ${diaEnPalabras(solicitud.fechaOperativa)}',
              style: estilo.titleMedium),
          Text('Sale de ${solicitud.bodega ?? 'la bodega principal'}. Cambia lo que no '
              'se pueda surtir; 0 lo quita.'),
          if ((solicitud.observaciones ?? '').isNotEmpty) Text('«${solicitud.observaciones}»'),
          if (espera != null) ...[
            const SizedBox(height: 8),
            Container(
              key: const Key('espera_corte'),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: colores.errorContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                'Primero cierra su corte del ${diaEnPalabras(espera)} (pestaña «Corte '
                'del día»): con su día abierto, la carga de mañana no sale.',
                style: TextStyle(color: colores.onErrorContainer),
              ),
            ),
          ],
          const SizedBox(height: 8),
          for (final r in solicitud.renglones)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(r.nombre),
                        Text(
                          'Pidió ${r.bultos.textoCorto} ${r.unidad} '
                          '(${r.cantidad.textoCorto} ${r.unidadBase}) · '
                          'en bodega ${r.enBodega.textoCorto}',
                          style: TextStyle(
                            fontSize: 12,
                            color: r.faltaEnBodega ? colores.error : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                  SizedBox(
                    width: 72,
                    child: TextField(
                      key: Key('aceptar_${r.sku}'),
                      controller: _bultos[r.productoId],
                      enabled: solicitud.pendiente && !_ocupado,
                      keyboardType: TextInputType.number,
                      inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                      textAlign: TextAlign.end,
                      decoration: const InputDecoration(
                        isDense: true,
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 6),
                  Text(r.unidad),
                ],
              ),
            ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, key: const Key('error_carga_pedida'),
                style: TextStyle(color: colores.error)),
          ],
          const SizedBox(height: 16),
          if (solicitud.pendiente) ...[
            FilledButton.icon(
              key: const Key('boton_aceptar_carga'),
              onPressed: _ocupado || espera != null ? null : _aceptar,
              icon: const Icon(Icons.check),
              label: const Text('Aceptar y confirmar la carga'),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              key: const Key('boton_rechazar_solicitud'),
              onPressed: _ocupado ? null : _rechazar,
              icon: const Icon(Icons.block),
              label: const Text('Rechazar la solicitud'),
            ),
          ],
        ],
      ),
    );
  }
}

/// El motivo del rechazo: al vendedor le llega tal cual.
class _DialogoRechazo extends StatefulWidget {
  const _DialogoRechazo();

  @override
  State<_DialogoRechazo> createState() => _EstadoDialogoRechazo();
}

class _EstadoDialogoRechazo extends State<_DialogoRechazo> {
  final _motivo = TextEditingController();

  @override
  void dispose() {
    _motivo.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Rechazar la solicitud'),
        content: TextField(
          key: const Key('campo_motivo_rechazo'),
          controller: _motivo,
          maxLength: 300,
          decoration: const InputDecoration(
            labelText: '¿Por qué? Le llega al vendedor',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancelar')),
          FilledButton(
            key: const Key('boton_rechazar_de_verdad'),
            onPressed: () {
              final m = _motivo.text.trim();
              if (m.isNotEmpty) Navigator.of(context).pop(m);
            },
            child: const Text('Rechazar'),
          ),
        ],
      );
}
