/// Entradas de mercancía desde el teléfono de la oficina.
///
/// El mismo camino que el panel —el servidor usa las mismas funciones—:
/// abrir (a qué bodega y por qué) → capturar lo que llegó → confirmar. Hasta
/// confirmar no entra nada; confirmar escribe el libro mayor, recalcula el
/// costo promedio y, si es compra a un proveedor del catálogo, deja la cuenta
/// por pagar.
///
/// Arriba va «Recibir compra», que funciona sin señal (ADR 0002 §83): ver
/// `compras.dart`.
///
/// Las reglas las pone el servidor y esta pantalla muestra su mensaje tal cual:
/// bultos enteros, la compra exige costo por bulto, el inventario inicial
/// exige nota, el producto con lote exige lote, y al camión no se recibe.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/almacen.dart';
import '../../estado/vendedores.dart';
import '../canceladas.dart';
import 'almacen.dart';
import 'comunes.dart';
import 'compras.dart';

String _estado(String estado) => switch (estado) {
      'borrador' => 'capturando',
      'confirmada' => 'entró',
      'cancelada' => 'cancelada',
      _ => estado,
    };

// ---------------------------------------------------------------------------
// La lista
// ---------------------------------------------------------------------------
class PantallaEntradas extends ConsumerStatefulWidget {
  const PantallaEntradas({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaEntradas> createState() => _EstadoEntradas();
}

class _EstadoEntradas extends ConsumerState<PantallaEntradas> {
  ListaDeEntradas? _lista;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    // Lo que el gerente capturó sin señal se manda en cuanto se puede (§83).
    // Va antes de pedir la lista para que lo recién recibido salga en ella.
    await enviarComprasPendientes(ref);
    if (!mounted) return;
    try {
      final l = await cliente.entradas();
      if (!mounted) return;
      setState(() {
        _lista = l;
        _error = null;
      });
      // Con señal: que el teléfono tenga el catálogo para la próxima compra
      // que llegue donde no la hay.
      await refrescarCatalogoDeCompras(ref);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _abrirDetalle(String id, {DetalleDeEntrada? inicial}) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => PantallaEntrada(entradaId: id, inicial: inicial),
      ),
    );
    await _cargar();
  }

  Future<void> _nueva(ListaDeEntradas lista) async {
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) return;
    if (lista.bodegas.isEmpty) {
      _avisar('No hay bodegas dadas de alta. Se dan de alta en el panel.');
      return;
    }
    final nueva = await showModalBottomSheet<NuevaEntrada>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _NuevaEntrada(lista: lista),
    );
    if (nueva == null || !mounted) return;
    try {
      final entrada = await cliente.abrirEntrada(nueva);
      if (!mounted) return;
      await _abrirDetalle(entrada.id, inicial: entrada);
    } on Object catch (e) {
      if (!mounted) return;
      _avisar(explicarErrorDeOficina(e));
    }
  }

  void _avisar(String texto) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(texto), duration: const Duration(seconds: 6)));

  @override
  Widget build(BuildContext context) {
    final l = _lista;
    final estilo = Theme.of(context).textTheme;
    final borradores = [
      for (final e in l?.entradas ?? const <EntradaEnLista>[])
        if (e.estado == 'borrador') e,
    ];
    final hechas = [
      for (final e in l?.entradas ?? const <EntradaEnLista>[])
        if (e.estado != 'borrador' && !estaCancelado(e.estado)) e,
    ];
    // Las canceladas, aparte y plegadas (ADR 0002 §95).
    final canceladas = [
      for (final e in l?.entradas ?? const <EntradaEnLista>[])
        if (estaCancelado(e.estado)) e,
    ];
    return Scaffold(
      key: const Key('pantalla_entradas'),
      appBar: !widget.conBarra ? null : AppBar(title: const Text('Entradas de mercancía')),
      floatingActionButton: l == null || !ref.watch(puedeAjustarProvider)
          ? null
          : FloatingActionButton.extended(
              key: const Key('boton_nueva_entrada'),
              onPressed: () => _nueva(l),
              icon: const Icon(Icons.add),
              label: const Text('Nueva entrada'),
            ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          key: const Key('lista_entradas'),
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            // Arriba y fuera de la lista del servidor: sin señal también se ve,
            // porque es justo cuando hace falta.
            const TarjetaComprasDelTelefono(),
            const SizedBox(height: 8),
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            if (l == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (l != null) ...[
              if (borradores.isNotEmpty) ...[
                Text('Capturando (todavía no entra)', style: estilo.titleMedium),
                for (final e in borradores) _renglon(e),
                const SizedBox(height: 16),
              ],
              Text('Recientes', style: estilo.titleMedium),
              if (hechas.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 8),
                  child: Text('Todavía no hay entradas.'),
                ),
              for (final e in hechas) _renglon(e),
              SeccionDeCanceladas(renglones: [for (final e in canceladas) _renglon(e)]),
            ],
          ],
        ),
      ),
    );
  }

  Widget _renglon(EntradaEnLista e) {
    final quien = [e.proveedor, e.referencia].whereType<String>().join(' · ');
    return Card(
      child: ListTile(
        key: Key('entrada_${e.folio}'),
        leading: Icon(switch (e.estado) {
          'borrador' => Icons.edit_note,
          'cancelada' => Icons.cancel_outlined,
          _ => Icons.move_to_inbox_outlined,
        }),
        title: Text('${e.motivoEtiqueta} · ${e.bodega}'),
        subtitle: Text(
          '${e.folio} · ${diaEnPalabras(e.fecha)} · ${_estado(e.estado)}'
          '${quien.isEmpty ? '' : '\n$quien'}'
          '\n${e.renglones} producto(s) · ${cantidadLegible(e.piezas)} piezas'
          '${e.importeTotal == null ? '' : ' · ${pesos(e.importeTotal!)}'}',
        ),
        isThreeLine: true,
        trailing: const Icon(Icons.chevron_right),
        onTap: () => _abrirDetalle(e.id),
      ),
    );
  }
}

/// Por qué entra, a qué bodega, y de quién (si es compra).
class _NuevaEntrada extends StatefulWidget {
  const _NuevaEntrada({required this.lista});

  final ListaDeEntradas lista;

  @override
  State<_NuevaEntrada> createState() => _EstadoNuevaEntrada();
}

class _EstadoNuevaEntrada extends State<_NuevaEntrada> {
  late String _motivo = widget.lista.motivos.any((m) => m.clave == 'compra')
      ? 'compra'
      : widget.lista.motivos.first.clave;
  late String _bodega = widget.lista.bodegas.first.id;

  /// El id del proveedor del catálogo, o vacío para escribirlo a mano.
  String _proveedor = '';
  final _proveedorEscrito = TextEditingController();
  final _referencia = TextEditingController();
  final _nota = TextEditingController();

  @override
  void dispose() {
    _proveedorEscrito.dispose();
    _referencia.dispose();
    _nota.dispose();
    super.dispose();
  }

  MotivoDeEntrada get _elegido => widget.lista.motivos.firstWhere((m) => m.clave == _motivo);

  @override
  Widget build(BuildContext context) {
    final motivo = _elegido;
    final faltaNota = motivo.exigeNota && _nota.text.trim().isEmpty;
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Nueva entrada', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              key: const Key('campo_motivo_entrada'),
              initialValue: _motivo,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: '¿Por qué entra?',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final m in widget.lista.motivos)
                  DropdownMenuItem(value: m.clave, child: Text(m.etiqueta)),
              ],
              onChanged: (m) => setState(() => _motivo = m ?? _motivo),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(4, 4, 4, 0),
              child: Text(motivo.explicacion, style: Theme.of(context).textTheme.bodySmall),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: const Key('campo_bodega_entrada'),
              initialValue: _bodega,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Entra a la bodega',
                border: OutlineInputBorder(),
              ),
              items: [
                for (final b in widget.lista.bodegas)
                  DropdownMenuItem(value: b.id, child: Text(b.nombre)),
              ],
              onChanged: (b) => setState(() => _bodega = b ?? _bodega),
            ),
            if (motivo.exigeCosto) ...[
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                key: const Key('campo_proveedor_entrada'),
                initialValue: _proveedor,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Proveedor',
                  border: OutlineInputBorder(),
                ),
                items: [
                  for (final p in widget.lista.proveedores)
                    DropdownMenuItem(value: p.id, child: Text(p.nombre)),
                  const DropdownMenuItem(value: '', child: Text('Otro (escribirlo)')),
                ],
                onChanged: (p) => setState(() => _proveedor = p ?? ''),
              ),
              if (_proveedor.isEmpty) ...[
                const SizedBox(height: 8),
                TextField(
                  key: const Key('campo_proveedor_texto'),
                  controller: _proveedorEscrito,
                  decoration: const InputDecoration(
                    labelText: 'Nombre del proveedor (opcional)',
                    helperText: 'Sin proveedor del catálogo no queda cuenta por pagar.',
                    border: OutlineInputBorder(),
                  ),
                ),
              ],
              const SizedBox(height: 8),
              TextField(
                key: const Key('campo_referencia_entrada'),
                controller: _referencia,
                decoration: const InputDecoration(
                  labelText: 'Remisión o factura (opcional)',
                  border: OutlineInputBorder(),
                ),
              ),
            ],
            const SizedBox(height: 12),
            TextField(
              key: const Key('campo_nota_entrada'),
              controller: _nota,
              maxLength: 500,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                labelText: motivo.exigeNota ? 'Nota (obligatoria)' : 'Nota (opcional)',
                helperText: motivo.exigeNota
                    ? 'Explica de dónde salió el inventario del arranque.'
                    : null,
                border: const OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            FilledButton(
              key: const Key('boton_abrir_entrada'),
              onPressed: faltaNota
                  ? null
                  : () => Navigator.of(context).pop(
                        NuevaEntrada(
                          bodegaId: _bodega,
                          motivo: _motivo,
                          proveedorId:
                              motivo.exigeCosto && _proveedor.isNotEmpty ? _proveedor : null,
                          proveedor: motivo.exigeCosto && _proveedor.isEmpty
                              ? _proveedorEscrito.text.trim()
                              : '',
                          referencia: motivo.exigeCosto ? _referencia.text.trim() : '',
                          nota: _nota.text.trim(),
                        ),
                      ),
              child: const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Abrir la entrada'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Una entrada: capturar, confirmar, cancelar
// ---------------------------------------------------------------------------
class PantallaEntrada extends ConsumerStatefulWidget {
  const PantallaEntrada({super.key, required this.entradaId, this.inicial});

  final String entradaId;

  /// La que contestó el servidor al abrirla: se pinta sin volver a pedirla.
  final DetalleDeEntrada? inicial;

  @override
  ConsumerState<PantallaEntrada> createState() => _EstadoEntrada();
}

class _EstadoEntrada extends ConsumerState<PantallaEntrada> {
  late DetalleDeEntrada? _entrada = widget.inicial;
  String? _error;
  bool _ocupado = false;

  /// El catálogo con lo que hay en la bodega. Se pide al primer «Agregar».
  List<ProductoParaCapturar>? _catalogo;

  @override
  void initState() {
    super.initState();
    if (_entrada == null) {
      WidgetsBinding.instance.addPostFrameCallback(
        (_) => _hacer((c) => c.verEntrada(widget.entradaId)),
      );
    }
  }

  /// Toda acción pasa por aquí: el servidor contesta con la entrada completa y
  /// su mensaje, y eso es lo que se pinta.
  Future<bool> _hacer(Future<DetalleDeEntrada> Function(ClienteAlmacen) accion) async {
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return false;
    }
    setState(() => _ocupado = true);
    try {
      final e = await accion(cliente);
      if (!mounted) return true;
      setState(() {
        _entrada = e;
        _error = null;
        _ocupado = false;
      });
      return true;
    } on Object catch (e) {
      if (!mounted) return false;
      setState(() {
        _error = explicarErrorDeOficina(e);
        _ocupado = false;
      });
      return false;
    }
  }

  Future<void> _agregar(DetalleDeEntrada entrada) async {
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) return;
    var catalogo = _catalogo;
    if (catalogo == null) {
      try {
        catalogo = await cliente.productos(entrada.bodegaId);
      } on Object catch (e) {
        if (!mounted) return;
        setState(() => _error = explicarErrorDeOficina(e));
        return;
      }
      _catalogo = catalogo;
    }
    if (!mounted) return;
    if (catalogo.isEmpty) {
      setState(() => _error = 'No hay productos activos en el catálogo. Se dan de alta '
          'en el panel (Catálogo).');
      return;
    }
    final producto = await elegirProducto(context, catalogo, titulo: '¿Qué llegó?');
    if (producto == null || !mounted) return;
    final renglon = await showModalBottomSheet<RenglonPorRecibir>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _CapturaDeRenglon(producto: producto, exigeCosto: entrada.exigeCosto),
    );
    if (renglon == null || !mounted) return;
    final ok = await _hacer((c) => c.agregarRenglon(entrada.id, renglon));
    // Lo que había en la bodega no cambia hasta confirmar, pero el renglón sí:
    // al volver a elegir, el catálogo se pide de nuevo por si alguien más movió.
    if (ok) _catalogo = null;
  }

  Future<void> _confirmar(DetalleDeEntrada e) async {
    final seguro = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        title: const Text('¿Confirmar la entrada?'),
        content: Text(
          'Entran ${cantidadLegible(e.totalPiezas)} piezas a ${e.bodega}'
          '${e.exigeCosto ? ', por ${pesos(e.importeCapturado)}' : ''}. '
          'Después ya no se edita.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(contexto).pop(false),
            child: const Text('Todavía no'),
          ),
          FilledButton(
            key: const Key('boton_confirmar_entrada_de_verdad'),
            onPressed: () => Navigator.of(contexto).pop(true),
            child: const Text('Confirmar'),
          ),
        ],
      ),
    );
    if (seguro == true) await _hacer((c) => c.confirmarEntrada(e.id));
  }

  Future<void> _cancelar(DetalleDeEntrada e) async {
    final motivo = await showDialog<String>(
      context: context,
      builder: (_) => const _DialogoCancelar(),
    );
    if (motivo == null) return;
    await _hacer((c) => c.cancelarEntrada(e.id, motivo: motivo));
  }

  @override
  Widget build(BuildContext context) {
    final e = _entrada;
    final estilo = Theme.of(context).textTheme;
    final puede = ref.watch(puedeAjustarProvider);
    final editable = e != null && e.editable && puede;
    return Scaffold(
      key: const Key('pantalla_entrada'),
      appBar: AppBar(
        title: Text(e?.folio ?? 'Entrada'),
        actions: [
          if (editable)
            IconButton(
              key: const Key('boton_cancelar_entrada'),
              tooltip: 'Cancelar la entrada',
              icon: const Icon(Icons.delete_outline),
              onPressed: _ocupado ? null : () => _cancelar(e),
            ),
        ],
      ),
      bottomNavigationBar: !editable
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const Key('boton_agregar_a_la_entrada'),
                        onPressed: _ocupado ? null : () => _agregar(e),
                        icon: const Icon(Icons.add),
                        label: const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('Agregar'),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton(
                        key: const Key('boton_confirmar_entrada'),
                        onPressed: _ocupado || e.renglones.isEmpty ? null : () => _confirmar(e),
                        child: const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('Confirmar'),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
      body: e == null
          ? Center(
              child: _error == null
                  ? const CircularProgressIndicator()
                  : Padding(
                      padding: const EdgeInsets.all(16),
                      child: AvisoDeOficina(_error!, esError: true),
                    ),
            )
          : ListView(
              key: const Key('lista_entrada'),
              padding: const EdgeInsets.all(16),
              children: [
                Text('${e.motivoEtiqueta} · ${e.bodega}', style: estilo.titleMedium),
                Text('${diaEnPalabras(e.fecha)} · ${_estado(e.estado)}'),
                if (e.proveedor != null || e.referencia != null)
                  Text([e.proveedor, e.referencia].whereType<String>().join(' · ')),
                if (e.nota != null) Text('Nota: ${e.nota}', style: estilo.bodySmall),
                const SizedBox(height: 12),
                if (_error != null)
                  AvisoDeOficina(_error!, key: const Key('aviso_entrada_error'), esError: true),
                if (e.mensaje != null && _error == null)
                  AvisoDeOficina(e.mensaje!, key: const Key('aviso_entrada')),
                if (e.editable && e.exigeCosto && e.sinCosto > 0)
                  AvisoDeOficina(
                    '${e.sinCosto} renglón(es) sin costo: una compra no se confirma sin '
                    'el costo de cada producto. Quítalo y vuélvelo a capturar con su costo.',
                    esError: true,
                  ),
                if (e.cuenta != null)
                  AvisoDeOficina(
                    'Cuenta por pagar a ${e.cuenta!.proveedor}: ${pesos(e.cuenta!.saldo)} '
                    'de ${pesos(e.cuenta!.importeOriginal)}, vence '
                    '${diaEnPalabras(e.cuenta!.vence)}.',
                    key: const Key('aviso_cuenta_por_pagar'),
                  ),
                Text('Lo que llegó', style: estilo.titleSmall),
                if (e.renglones.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text('Todavía nada. Toca «Agregar» y elige el producto.'),
                  ),
                for (final r in e.renglones)
                  ListTile(
                    key: Key('renglon_entrada_${r.sku}'),
                    contentPadding: EdgeInsets.zero,
                    title: Text(r.nombre),
                    subtitle: Text(
                      [
                        if (r.bultos != null && r.unidad != null && r.unidad != r.unidadBase)
                          '${cantidadLegible(r.bultos!)} ${r.unidad} = '
                              '${cantidadLegible(r.cantidad)} ${r.unidadBase}'
                        else
                          '${cantidadLegible(r.cantidad)} ${r.unidadBase}',
                        if (r.importe != null) pesos(r.importe!),
                        if (r.lote != null) 'lote ${r.lote}',
                        if (e.editable)
                          'hay ${cantidadLegible(r.existencia)} → '
                              'habrá ${cantidadLegible(r.proyectado)}',
                      ].join(' · '),
                    ),
                    trailing: editable
                        ? IconButton(
                            key: Key('quitar_${r.sku}'),
                            tooltip: 'Quitar',
                            icon: const Icon(Icons.remove_circle_outline),
                            onPressed: _ocupado
                                ? null
                                : () => _hacer((c) => c.quitarRenglon(e.id, r.id)),
                          )
                        : null,
                  ),
                if (e.renglones.isNotEmpty) ...[
                  const Divider(),
                  Text(
                    'Total: ${cantidadLegible(e.totalPiezas)} piezas'
                    '${e.importeCapturado.centavos > 0 ? ' · ${pesos(e.importeCapturado)}' : ''}',
                    key: const Key('total_entrada'),
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ],
                const SizedBox(height: 24),
              ],
            ),
    );
  }
}

/// Cuántos bultos de qué presentación, y a cuánto cada uno.
class _CapturaDeRenglon extends StatefulWidget {
  const _CapturaDeRenglon({required this.producto, required this.exigeCosto});

  final ProductoParaCapturar producto;
  final bool exigeCosto;

  @override
  State<_CapturaDeRenglon> createState() => _EstadoCaptura();
}

class _EstadoCaptura extends State<_CapturaDeRenglon> {
  // La presentación grande primero: así llega la mercancía y así viene en la
  // remisión.
  late String _unidad = widget.producto.presentaciones.isEmpty
      ? widget.producto.unidadBase
      : widget.producto.presentaciones.first.unidad;
  final _cantidad = TextEditingController();
  final _costo = TextEditingController();
  final _lote = TextEditingController();
  final _caducidad = TextEditingController();

  @override
  void dispose() {
    _cantidad.dispose();
    _costo.dispose();
    _lote.dispose();
    _caducidad.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.producto;
    final unidades = [for (final u in p.presentaciones) u.unidad];
    final factor = [
      for (final u in p.presentaciones)
        if (u.unidad == _unidad) cantidadLegible(u.factor),
    ];
    final listo = _cantidad.text.trim().isNotEmpty &&
        (!widget.exigeCosto || _costo.text.trim().isNotEmpty) &&
        (!p.manejaLote || _lote.text.trim().isNotEmpty);
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(p.nombre, style: Theme.of(context).textTheme.titleLarge),
            Text(
              '${p.sku} · hay ${cantidadLegible(p.existencia)} ${p.unidadBase} en la bodega'
              '${factor.isNotEmpty && factor.first != '1' ? '\n1 $_unidad = ${factor.first} ${p.unidadBase}' : ''}',
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    key: const Key('campo_cantidad_entrada'),
                    controller: _cantidad,
                    autofocus: true,
                    keyboardType: TextInputType.number,
                    onChanged: (_) => setState(() {}),
                    decoration: const InputDecoration(
                      labelText: 'Cuántos',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                DropdownButton<String>(
                  key: const Key('campo_unidad_entrada'),
                  value: unidades.contains(_unidad) ? _unidad : null,
                  items: [
                    for (final u in unidades) DropdownMenuItem(value: u, child: Text(u)),
                  ],
                  onChanged: (u) => setState(() => _unidad = u ?? _unidad),
                ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('campo_costo_entrada'),
              controller: _costo,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                labelText: widget.exigeCosto
                    ? 'Costo por $_unidad (obligatorio)'
                    : 'Costo por $_unidad (opcional)',
                prefixText: r'$ ',
                helperText: 'El de la remisión, por bulto: el sistema lo pasa a pieza.',
                border: const OutlineInputBorder(),
              ),
            ),
            if (p.manejaLote) ...[
              const SizedBox(height: 12),
              TextField(
                key: const Key('campo_lote_entrada'),
                controller: _lote,
                onChanged: (_) => setState(() {}),
                decoration: const InputDecoration(
                  labelText: 'Lote (obligatorio)',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                key: const Key('campo_caducidad_entrada'),
                controller: _caducidad,
                keyboardType: TextInputType.datetime,
                decoration: const InputDecoration(
                  labelText: 'Caducidad (AAAA-MM-DD, opcional)',
                  border: OutlineInputBorder(),
                ),
              ),
            ],
            const SizedBox(height: 16),
            FilledButton(
              key: const Key('boton_agregar_renglon_entrada'),
              onPressed: !listo
                  ? null
                  : () => Navigator.of(context).pop(
                        RenglonPorRecibir(
                          sku: p.sku,
                          unidad: _unidad,
                          cantidad: _cantidad.text.trim(),
                          costo: _costo.text.trim(),
                          lote: _lote.text.trim(),
                          caducidad: _caducidad.text.trim(),
                        ),
                      ),
              child: const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Agregar'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Cancelar pide por qué. Dueño de su campo de texto: el controlador vive lo
/// que vive el diálogo, incluida la animación de salida.
class _DialogoCancelar extends StatefulWidget {
  const _DialogoCancelar();

  @override
  State<_DialogoCancelar> createState() => _EstadoDialogoCancelar();
}

class _EstadoDialogoCancelar extends State<_DialogoCancelar> {
  final _motivo = TextEditingController();

  @override
  void dispose() {
    _motivo.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('¿Cancelar esta entrada?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('No entró nada todavía: solo se descarta lo capturado.'),
          const SizedBox(height: 12),
          TextField(
            key: const Key('campo_motivo_cancelar_entrada'),
            controller: _motivo,
            autofocus: true,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              labelText: '¿Por qué se cancela?',
              border: OutlineInputBorder(),
            ),
          ),
        ],
      ),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('No')),
        FilledButton(
          key: const Key('boton_cancelar_entrada_de_verdad'),
          onPressed: _motivo.text.trim().isEmpty
              ? null
              : () => Navigator.of(context).pop(_motivo.text.trim()),
          child: const Text('Sí, cancelar'),
        ),
      ],
    );
  }
}
