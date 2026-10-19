/// Las ventas de mayoreo, desde la app de la oficina (ADR 0002 §105).
///
/// «El administrador puede hacer ventas a mayoreo al precio que él decida, y
/// los clientes él los maneja.»
///
///   · **La lista**: las ventas del periodo (el selector del tablero), con su
///     total; las canceladas, aparte.
///   · **Nueva venta**: el cliente de mayoreo (o uno nuevo ahí mismo), la bodega,
///     y los artículos por familia; cada renglón trae el precio de lista y se
///     cambia al que se decida. Al guardar sale el ticket para compartir.
///   · **Cancelar** devuelve la mercancía a la bodega y pide motivo.
///
/// En línea, como las cargas: la venta saca la mercancía en ese momento.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/oficina_extra.dart';
import '../../estado/sesion.dart';
import '../../estado/vendedores.dart';
import '../canceladas.dart';
import '../familias.dart';
import '../ticket_compartible.dart';
import 'comunes.dart';
import 'periodo.dart';
import '../confirmar.dart';

class PantallaMayoreo extends ConsumerStatefulWidget {
  const PantallaMayoreo({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaMayoreo> createState() => _EstadoMayoreo();
}

class _EstadoMayoreo extends ConsumerState<PantallaMayoreo> {
  PeriodoElegido _periodo = const PeriodoElegido('hoy');
  VentasDeMayoreo? _ventas;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteMayoreoProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    final periodo = _periodo;
    try {
      final ventas = await cliente.ventas(
        periodo: periodo.clave,
        desde: periodo.desde,
        hasta: periodo.hasta,
      );
      if (!mounted || periodo != _periodo) return;
      setState(() {
        _ventas = ventas;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _nueva() async {
    final hecha = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => const PantallaNuevaVentaMayoreo()),
    );
    if (hecha == true) await _cargar();
  }

  Future<void> _abrir(VentaDeMayoreoResumen v) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => _DetalleDeVenta(ventaId: v.id)),
    );
    await _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final ventas = _ventas;
    final vivas = [for (final v in ventas?.ventas ?? const <VentaDeMayoreoResumen>[])
      if (!estaCancelado(v.estado)) v];
    return Scaffold(
      key: const Key('pantalla_mayoreo'),
      appBar: widget.conBarra ? AppBar(title: const Text('Mayoreo')) : null,
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('boton_nueva_venta_mayoreo'),
        onPressed: _nueva,
        icon: const Icon(Icons.add_shopping_cart),
        label: const Text('Nueva venta'),
      ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            SelectorDePeriodo(
              periodos: periodosDeSiempre,
              elegido: _periodo,
              hoy: ref.watch(relojProvider)(),
              conUnDia: true,
              onElegir: (p) {
                setState(() {
                  _periodo = p;
                  _ventas = null;
                });
                _cargar();
              },
            ),
            const SizedBox(height: 12),
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            if (ventas == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (ventas != null) ...[
              Text(
                '${pesos(Dinero.deTexto(ventas.total))} en ${vivas.length} venta(s)',
                key: const Key('total_mayoreo'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
              if (vivas.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: Text(
                    'Ninguna venta de mayoreo en esas fechas.',
                    key: Key('mayoreo_vacio'),
                    textAlign: TextAlign.center,
                  ),
                ),
              for (final v in vivas) _renglon(v),
              SeccionDeCanceladas(
                renglones: [
                  for (final v in ventas.ventas)
                    if (estaCancelado(v.estado)) _renglon(v),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _renglon(VentaDeMayoreoResumen v) => Card(
        child: ListTile(
          key: Key('venta_mayoreo_${v.folio}'),
          title: Text(v.cliente),
          subtitle: Text(
            '${v.folio} · ${diaEnPalabras(v.fecha)} · ${cantidadLegible(v.piezas)} piezas',
          ),
          trailing: Text(
            pesos(Dinero.deTexto(v.total)),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          onTap: () => _abrir(v),
        ),
      );
}

// ---------------------------------------------------------------------------
// Una venta: su ticket, compartirla y cancelarla
// ---------------------------------------------------------------------------

class _DetalleDeVenta extends ConsumerStatefulWidget {
  const _DetalleDeVenta({required this.ventaId});

  final String ventaId;

  @override
  ConsumerState<_DetalleDeVenta> createState() => _EstadoDetalle();
}

class _EstadoDetalle extends ConsumerState<_DetalleDeVenta> {
  VentaDeMayoreo? _venta;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar([Future<VentaDeMayoreo> Function(ClienteMayoreo c)? accion]) async {
    final cliente = ref.read(clienteMayoreoProvider);
    if (cliente == null) return;
    try {
      final venta = await (accion ?? (c) => c.ver(widget.ventaId))(cliente);
      if (!mounted) return;
      setState(() {
        _venta = venta;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _cancelar() async {
    final motivo = TextEditingController();
    final si = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        title: const Text('Cancelar la venta'),
        content: TextField(
          key: const Key('motivo_cancelar_mayoreo'),
          controller: motivo,
          autofocus: true,
          decoration: const InputDecoration(labelText: 'Por qué se cancela'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(contexto, false), child: const Text('No')),
          FilledButton(
            key: const Key('confirmar_cancelar_mayoreo'),
            onPressed: () => Navigator.pop(contexto, true),
            child: const Text('Cancelar la venta'),
          ),
        ],
      ),
    );
    final texto = motivo.text;
    motivo.dispose();
    if (si != true || !mounted) return;
    await _cargar((c) => c.cancelar(widget.ventaId, texto));
  }

  @override
  Widget build(BuildContext context) {
    final venta = _venta;
    return Scaffold(
      key: const Key('detalle_venta_mayoreo'),
      appBar: AppBar(title: Text(venta?.folio ?? 'Venta de mayoreo')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) AvisoDeOficina(_error!, esError: true),
          if (venta == null && _error == null)
            const Center(child: CircularProgressIndicator()),
          if (venta != null) ...[
            if (estaCancelado(venta.estado))
              AvisoDeOficina(
                'Cancelada: ${venta.motivoCancelacion ?? ''}. La mercancía volvió a la bodega.',
                esError: true,
              ),
            SelectableText(venta.ticket, key: const Key('ticket_mayoreo')),
            const SizedBox(height: 16),
            FilledButton.icon(
              key: const Key('compartir_mayoreo'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => PantallaTicketCompartible(
                    titulo: 'Venta ${venta.folio}',
                    texto: venta.ticket,
                  ),
                ),
              ),
              icon: const Icon(Icons.share_outlined),
              label: const Text('Compartir el ticket'),
            ),
            if (!estaCancelado(venta.estado)) ...[
              const SizedBox(height: 8),
              OutlinedButton.icon(
                key: const Key('boton_cancelar_mayoreo'),
                onPressed: _cancelar,
                icon: const Icon(Icons.undo),
                label: const Text('Cancelar la venta'),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Nueva venta
// ---------------------------------------------------------------------------

class _Renglon {
  _Renglon(this.producto) : presentacion = _masGrande(producto) {
    precio.text = presentacion.precioLista ?? '';
  }

  final ProductoDeBodega producto;
  PresentacionDeMayoreo presentacion;
  final bultos = TextEditingController(text: '1');
  final precio = TextEditingController();

  /// En la bodega se vende por caja: la presentación más grande, de entrada.
  static PresentacionDeMayoreo _masGrande(ProductoDeBodega p) => p.presentaciones.reduce(
        (a, b) => (double.tryParse(b.factor) ?? 1) > (double.tryParse(a.factor) ?? 1) ? b : a,
      );

  int get cuantos => int.tryParse(bultos.text.trim()) ?? 0;

  Dinero? get precioDinero {
    final limpio = precio.text.trim().replaceAll(',', '').replaceAll(r'$', '');
    final m = RegExp(r'^(\d{1,9})(?:\.(\d{1,4}))?$').firstMatch(limpio);
    if (m == null) return null;
    return Dinero.deTexto('${m.group(1)}.${(m.group(2) ?? '').padRight(2, '0').substring(0, 2)}');
  }

  void dispose() {
    bultos.dispose();
    precio.dispose();
  }
}

class PantallaNuevaVentaMayoreo extends ConsumerStatefulWidget {
  const PantallaNuevaVentaMayoreo({super.key});

  @override
  ConsumerState<PantallaNuevaVentaMayoreo> createState() => _EstadoNuevaVenta();
}

class _EstadoNuevaVenta extends ConsumerState<PantallaNuevaVentaMayoreo> {
  OpcionesDeMayoreo? _opciones;
  List<ProductoDeBodega> _surtido = const [];
  String? _cliente;
  String? _bodega;
  String _formaPago = 'efectivo';
  final _referencia = TextEditingController();
  final _busqueda = TextEditingController();
  final List<_Renglon> _renglones = [];
  String? _error;
  bool _guardando = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargarOpciones());
  }

  @override
  void dispose() {
    _referencia.dispose();
    _busqueda.dispose();
    for (final r in _renglones) {
      r.dispose();
    }
    super.dispose();
  }

  ClienteMayoreo? get _api => ref.read(clienteMayoreoProvider);

  Future<void> _cargarOpciones() async {
    final api = _api;
    if (api == null) return;
    try {
      final opciones = await api.opciones();
      if (!mounted) return;
      setState(() {
        _opciones = opciones;
        _bodega ??= opciones.bodegas.isEmpty ? null : opciones.bodegas.first.id;
      });
      await _cargarSurtido();
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _cargarSurtido() async {
    final api = _api;
    final bodega = _bodega;
    if (api == null || bodega == null) return;
    try {
      final surtido = await api.surtido(bodega);
      if (!mounted) return;
      setState(() => _surtido = surtido);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _clienteNuevo() async {
    final campos = [for (var i = 0; i < 4; i++) TextEditingController()];
    final si = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        title: const Text('Cliente de mayoreo nuevo'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final (i, etiqueta) in const [
                'Nombre del negocio',
                'Teléfono',
                'Calle y número',
                'Colonia',
              ].indexed)
                TextField(
                  key: Key('campo_cliente_mayoreo_$i'),
                  controller: campos[i],
                  autofocus: i == 0,
                  keyboardType: i == 1 ? TextInputType.phone : TextInputType.text,
                  decoration: InputDecoration(labelText: etiqueta),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(contexto, false), child: const Text('No')),
          FilledButton(
            key: const Key('guardar_cliente_mayoreo'),
            onPressed: () => Navigator.pop(contexto, true),
            child: const Text('Dar de alta'),
          ),
        ],
      ),
    );
    final valores = [for (final c in campos) c.text];
    for (final c in campos) {
      c.dispose();
    }
    final api = _api;
    if (si != true || api == null || !mounted) return;
    try {
      final nuevo = await api.altaDeCliente(
        nombre: valores[0],
        telefono: valores[1],
        calle: valores[2],
        colonia: valores[3],
      );
      final opciones = await api.opciones();
      if (!mounted) return;
      setState(() {
        _opciones = opciones;
        _cliente = nuevo.id;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  void _agregar(ProductoDeBodega p) => setState(() {
        _renglones.add(_Renglon(p));
        _busqueda.clear();
      });

  Dinero get _total => _renglones.fold(
        Dinero.cero,
        (suma, r) => suma + _veces(r.precioDinero ?? Dinero.cero, r.cuantos),
      );

  Future<void> _guardar() async {
    final api = _api;
    if (api == null) return;
    String? problema;
    if (_cliente == null) problema = 'Escoge el cliente.';
    if (_renglones.isEmpty) problema ??= 'Agrega al menos un artículo.';
    for (final r in _renglones) {
      if (r.cuantos <= 0) problema ??= '${r.producto.nombre}: falta la cantidad.';
      if (r.precioDinero == null || r.precioDinero!.esCero) {
        problema ??= '${r.producto.nombre}: falta el precio.';
      }
    }
    if (problema != null) {
      setState(() => _error = problema);
      return;
    }
    setState(() {
      _guardando = true;
      _error = null;
    });
    try {
      final venta = await api.vender(
        clienteId: _cliente!,
        bodegaId: _bodega!,
        formaPago: _formaPago,
        referenciaPago: _referencia.text,
        renglones: [
          for (final r in _renglones)
            RenglonDeMayoreo(
              productoId: r.producto.productoId,
              unidad: r.presentacion.unidad,
              bultos: r.cuantos,
              precio: r.precioDinero!.texto,
            ),
        ],
      );
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => PantallaTicketCompartible(
            titulo: 'Venta ${venta.folio}',
            texto: venta.ticket,
            aviso: 'Venta guardada: la mercancía ya salió de la bodega.',
          ),
        ),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _guardando = false;
        _error = explicarErrorDeOficina(e);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final opciones = _opciones;
    final filtro = _busqueda.text.trim().toLowerCase();
    final candidatos = [
      for (final p in _surtido)
        if (filtro.isEmpty ||
            p.nombre.toLowerCase().contains(filtro) ||
            p.sku.toLowerCase().contains(filtro))
          p,
    ];
    return Scaffold(
      key: const Key('pantalla_nueva_venta_mayoreo'),
      appBar: AppBar(title: const Text('Venta de mayoreo')),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
          child: FilledButton(
            key: const Key('boton_guardar_mayoreo'),
            onPressed: _guardando ? null : _guardar,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(
                _guardando ? 'Guardando…' : 'Guardar venta · ${pesos(_total)}',
                key: const Key('total_venta_mayoreo'),
              ),
            ),
          ),
        ),
      ),
      body: opciones == null
          ? Center(
              child: _error != null
                  ? Padding(
                      padding: const EdgeInsets.all(16),
                      child: AvisoDeOficina(_error!, esError: true),
                    )
                  : const CircularProgressIndicator(),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Row(
                  children: [
                    Expanded(
                      child: DropdownButtonFormField<String>(
                        key: const Key('campo_cliente_mayoreo'),
                        initialValue: _cliente,
                        isExpanded: true,
                        decoration: const InputDecoration(
                          labelText: 'Cliente',
                          border: OutlineInputBorder(),
                        ),
                        items: [
                          for (final c in opciones.clientes)
                            DropdownMenuItem(value: c.id, child: Text(c.nombre)),
                        ],
                        onChanged: (v) => setState(() => _cliente = v),
                      ),
                    ),
                    IconButton(
                      key: const Key('boton_cliente_mayoreo_nuevo'),
                      tooltip: 'Cliente nuevo',
                      icon: const Icon(Icons.person_add_alt),
                      onPressed: _clienteNuevo,
                    ),
                  ],
                ),
                if (opciones.clientes.isEmpty)
                  const Padding(
                    padding: EdgeInsets.only(top: 6),
                    child: Text('Todavía no hay clientes de mayoreo: dalo de alta con el botón.'),
                  ),
                const SizedBox(height: 12),
                if (opciones.bodegas.length > 1)
                  DropdownButtonFormField<String>(
                    key: const Key('campo_bodega_mayoreo'),
                    initialValue: _bodega,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Sale de',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      for (final b in opciones.bodegas)
                        DropdownMenuItem(value: b.id, child: Text(b.nombre)),
                    ],
                    onChanged: (v) {
                      setState(() {
                        _bodega = v;
                        for (final r in _renglones) {
                          r.dispose();
                        }
                        _renglones.clear();
                      });
                      _cargarSurtido();
                    },
                  ),
                for (final r in _renglones) _renglonDeVenta(r),
                const SizedBox(height: 8),
                TextField(
                  key: const Key('buscar_producto_mayoreo'),
                  controller: _busqueda,
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                    labelText: 'Agregar artículo',
                    prefixIcon: Icon(Icons.search),
                    border: OutlineInputBorder(),
                  ),
                ),
                EscogerPorFamilia<ProductoDeBodega>(
                  elementos: candidatos,
                  familiaDe: (p) => p.familia,
                  buscando: filtro.isNotEmpty,
                  renglon: (p) => ListTile(
                    key: Key('agregar_mayoreo_${p.sku}'),
                    dense: true,
                    title: Text(p.nombre),
                    subtitle: Text('${p.sku} · hay ${cantidadLegible(p.existencia)} pzas'),
                    trailing: const Icon(Icons.add),
                    onTap: () => _agregar(p),
                  ),
                ),
                const SizedBox(height: 12),
                SegmentedButton<String>(
                  key: const Key('forma_pago_mayoreo'),
                  segments: const [
                    ButtonSegment(value: 'efectivo', label: Text('Efectivo')),
                    ButtonSegment(value: 'transferencia', label: Text('Transferencia')),
                  ],
                  selected: {_formaPago},
                  onSelectionChanged: (v) => setState(() => _formaPago = v.single),
                ),
                if (_formaPago == 'transferencia')
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: TextField(
                      key: const Key('referencia_mayoreo'),
                      controller: _referencia,
                      decoration: const InputDecoration(
                        labelText: 'Referencia (opcional)',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: AvisoDeOficina(_error!, esError: true),
                  ),
              ],
            ),
    );
  }

  Widget _renglonDeVenta(_Renglon r) {
    final lista = r.presentacion.precioLista;
    return Card(
      key: Key('renglon_mayoreo_${r.producto.sku}'),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(r.producto.nombre, style: const TextStyle(fontWeight: FontWeight.w600)),
                ),
                IconButton(
                  tooltip: 'Quitar',
                  icon: const Icon(Icons.close),
                  onPressed: () async {
                    final si = await confirmarAntesDeQuitar(
                      context,
                      titulo: '¿Quitar ${r.producto.nombre} de la venta?',
                    );
                    if (!si || !mounted) return;
                    setState(() {
                      _renglones.remove(r);
                      r.dispose();
                    });
                  },
                ),
              ],
            ),
            Row(
              children: [
                SizedBox(
                  width: 64,
                  child: TextField(
                    key: Key('cantidad_mayoreo_${r.producto.sku}'),
                    controller: r.bultos,
                    keyboardType: TextInputType.number,
                    inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                    textAlign: TextAlign.end,
                    onChanged: (_) => setState(() {}),
                    decoration: const InputDecoration(isDense: true, border: OutlineInputBorder()),
                  ),
                ),
                const SizedBox(width: 6),
                if (r.producto.presentaciones.length > 1)
                  DropdownButton<String>(
                    value: r.presentacion.unidad,
                    items: [
                      for (final u in r.producto.presentaciones)
                        DropdownMenuItem(
                          value: u.unidad,
                          child: Text(u.factor == '1.000'
                              ? u.unidad
                              : '${u.unidad} ${cantidadLegible(u.factor)}'),
                        ),
                    ],
                    onChanged: (u) => setState(() {
                      r.presentacion = r.producto.presentaciones.firstWhere((x) => x.unidad == u);
                      r.precio.text = r.presentacion.precioLista ?? '';
                    }),
                  )
                else
                  Text(r.presentacion.unidad),
                const SizedBox(width: 6),
                Expanded(
                  child: TextField(
                    key: Key('precio_mayoreo_${r.producto.sku}'),
                    controller: r.precio,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    textAlign: TextAlign.end,
                    onChanged: (_) => setState(() {}),
                    decoration: const InputDecoration(
                      isDense: true,
                      prefixText: r'$',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ),
            if (lista != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Lista: ${pesos(Dinero.deTexto(lista))} por ${r.presentacion.unidad}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Un importe por una cantidad entera, desde los centavos: el dinero no pasa
/// por `double`.
Dinero _veces(Dinero precio, int cuantos) {
  final centavos = precio.centavos * cuantos;
  final signo = centavos < 0 ? '-' : '';
  final absoluto = centavos.abs();
  return Dinero.deTexto(
    '$signo${absoluto ~/ 100}.${(absoluto % 100).toString().padLeft(2, '0')}',
  );
}
