/// Cortes y cargas por aceptar: el cierre de cada vendedor, en la app del gerente.
///
/// El vendedor hace su corte y pide la carga de mañana desde su teléfono, sin
/// señal. Aquí el gerente los revisa juntos (ADR 0002 §82):
///
///   · **El corte**: el efectivo que entrega contra lo que vendió en efectivo, y
///     lo que contó arriba del camión contra lo que el sistema cree que trae. Lo
///     que no contó vale cero —un faltante— y se ve ANTES de aceptar.
///   · **La carga pedida**: producto por producto, con lo que hay en la bodega.
///     El gerente puede bajar o quitar un renglón antes de aceptar.
///
/// Aceptar cierra el corte (lo que falte va a la cuenta del vendedor) y confirma
/// la carga de mañana desde la bodega principal. Sale el ticket de la carga para
/// compartirlo. Solo hay una carga al día, y es para el día siguiente.
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

String _efectivoEnPalabras(CorteRecibido c) {
  final d = c.diferenciaEfectivo;
  final base = 'Entrega ${pesos(c.efectivoDeclarado)} de ${pesos(c.efectivoEsperado)}';
  if (d.esCero) return '$base · cuadra';
  return d.esNegativo ? '$base · faltan ${pesos(-d)}' : '$base · sobran ${pesos(d)}';
}

class PantallaCierres extends ConsumerStatefulWidget {
  const PantallaCierres({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaCierres> createState() => _EstadoCierres();
}

class _EstadoCierres extends ConsumerState<PantallaCierres> {
  Cierres? _cierres;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = _cliente(ref);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final c = await cliente.lista();
      if (!mounted) return;
      setState(() {
        _cierres = c;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _abrir(CierreDeVendedor c) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => PantallaCierre(cierre: c)),
    );
    await _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final c = _cierres;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    final cuerpo = RefreshIndicator(
      onRefresh: _cargar,
      child: ListView(
        key: const Key('lista_cierres'),
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) Text(_error!, style: TextStyle(color: colores.error)),
          if (c == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (c != null && c.pendientes.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 16),
              child: Text('Nada por aceptar: ningún vendedor ha mandado su corte o su '
                  'carga. Llegan cuando su teléfono sincroniza.'),
            ),
          for (final cierre in c?.pendientes ?? const <CierreDeVendedor>[])
            Card(
              child: ListTile(
                key: Key('cierre_${cierre.vendedorCodigo}'),
                title: Text(cierre.vendedor),
                subtitle: Text([
                  if (cierre.corte != null)
                    'Corte del ${diaEnPalabras(cierre.corte!.fechaOperativa)}: '
                        '${_efectivoEnPalabras(cierre.corte!)}'
                        '${cierre.corte!.conDiferencia.isEmpty ? '' : ' · ${cierre.corte!.conDiferencia.length} producto(s) no cuadran'}',
                  if (cierre.solicitud != null)
                    'Carga para el ${diaEnPalabras(cierre.solicitud!.fechaOperativa)}: '
                        '${cierre.solicitud!.renglones.length} producto(s)'
                  else
                    'No pidió carga',
                ].join('\n')),
                isThreeLine: true,
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _abrir(cierre),
              ),
            ),
          if (c != null && c.recientes.isNotEmpty) ...[
            const SizedBox(height: 16),
            Text('Resueltas esta semana', style: estilo.titleMedium),
            for (final r in c.recientes)
              ListTile(
                key: Key('cierre_reciente_${r.clave}'),
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
      ),
    );
    if (!widget.conBarra) return cuerpo;
    return Scaffold(
      key: const Key('pantalla_cierres'),
      appBar: AppBar(title: const Text('Cortes y cargas')),
      body: cuerpo,
    );
  }
}

class PantallaCierre extends ConsumerStatefulWidget {
  const PantallaCierre({super.key, required this.cierre});

  final CierreDeVendedor cierre;

  @override
  ConsumerState<PantallaCierre> createState() => _EstadoCierre();
}

class _EstadoCierre extends ConsumerState<PantallaCierre> {
  final Map<String, TextEditingController> _bultos = {};
  bool _ocupado = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    for (final r in widget.cierre.solicitud?.renglones ?? const <RenglonSolicitado>[]) {
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
    final cierre = widget.cierre;
    // Solo viaja lo que el gerente cambió: lo demás se acepta como se pidió.
    final cambios = <String, String>{};
    for (final r in cierre.solicitud?.renglones ?? const <RenglonSolicitado>[]) {
      final texto = _bultos[r.productoId]!.text.trim();
      final valor = texto.isEmpty ? '0' : texto;
      if (valor != r.bultos.textoCorto) cambios[r.productoId] = valor;
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final hecho = await cliente.aceptar(
        corteId: cierre.corte?.id,
        solicitudId: cierre.solicitud?.id,
        bultos: cambios,
      );
      if (!mounted) return;
      final ticket = hecho.ticketDeLaCarga;
      if (ticket == null) {
        Navigator.of(context).pop();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(hecho.mensaje ?? 'Corte cerrado.')),
        );
        return;
      }
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => PantallaTicketCompartible(
            titulo: 'Carga aceptada',
            texto: ticket,
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
    final corte = c.corte;
    final solicitud = c.solicitud;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    final cierraCorte = corte != null && corte.pendiente;

    return Scaffold(
      key: const Key('pantalla_cierre'),
      appBar: AppBar(title: Text(c.vendedor)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (c.camion != null) Text(c.camion!, style: estilo.titleSmall),
          if (corte != null) ...[
            const SizedBox(height: 8),
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
            if (!corte.pendiente)
              Text('Este corte ya está ${corte.estado}'
                  '${corte.liquidacionFolio == null ? '' : ' (${corte.liquidacionFolio})'}.'),
            const SizedBox(height: 8),
            for (final r in corte.renglones)
              ListTile(
                key: Key('contado_${r.sku}'),
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(r.nombre),
                subtitle: Text(r.contado
                    ? 'Contó ${r.contada.textoCorto} · el sistema dice ${r.sistema.textoCorto}'
                    : 'NO LO CONTÓ · el sistema dice ${r.sistema.textoCorto}'),
                trailing: Text(
                  r.diferencia.esCero
                      ? 'cuadra'
                      : '${r.diferencia.esNegativa ? '' : '+'}${r.diferencia.textoCorto}',
                  style: TextStyle(
                    color: r.diferencia.esNegativa ? colores.error : null,
                    fontWeight: r.diferencia.esCero ? null : FontWeight.w600,
                  ),
                ),
              ),
            if (corte.conDiferencia.any((r) => r.diferencia.esNegativa))
              const Text('Lo que falta se le carga al vendedor a costo al aceptar.'),
            const Divider(height: 32),
          ],
          if (solicitud != null) ...[
            Text('Carga para el ${diaEnPalabras(solicitud.fechaOperativa)}',
                style: estilo.titleMedium),
            Text('Sale de ${solicitud.bodega ?? 'la bodega principal'}. Cambia lo que no '
                'se pueda surtir; 0 lo quita.'),
            if ((solicitud.observaciones ?? '').isNotEmpty) Text('«${solicitud.observaciones}»'),
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
          ] else
            const Text('No pidió carga para mañana.'),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, key: const Key('error_cierre'), style: TextStyle(color: colores.error)),
          ],
          const SizedBox(height: 16),
          if ((solicitud?.pendiente ?? false) || cierraCorte)
            FilledButton.icon(
              key: const Key('boton_aceptar_cierre'),
              onPressed: _ocupado ? null : _aceptar,
              icon: const Icon(Icons.check),
              label: Text(
                solicitud != null && cierraCorte
                    ? 'Aceptar: cerrar el corte y confirmar la carga'
                    : solicitud != null
                        ? 'Aceptar y confirmar la carga'
                        : 'Cerrar el corte',
              ),
            ),
          if (solicitud?.pendiente ?? false) ...[
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
