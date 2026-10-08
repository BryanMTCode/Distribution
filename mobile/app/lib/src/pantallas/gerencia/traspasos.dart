/// Traspasos entre bodegas desde el teléfono de la oficina.
///
/// En un paso: se elige de dónde, a dónde y cuánto, y al tocar «Traspasar» la
/// mercancía sale de una y entra a la otra en la misma transacción. Solo entre
/// bodegas: al camión se le sube con una carga y baja con la devolución del
/// vendedor (§0.2). No deja la bodega de origen en negativo: si no alcanza, el
/// servidor dice cuánto hay y nada se mueve.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/almacen.dart';
import '../../estado/vendedores.dart';
import 'almacen.dart';
import 'comunes.dart';

String _cuando(DateTime momento) {
  final local = momento.toLocal();
  final hora = '${local.hour.toString().padLeft(2, '0')}:'
      '${local.minute.toString().padLeft(2, '0')}';
  return '${diaEnPalabras(diaOperativoDe(local))}, $hora';
}

// ---------------------------------------------------------------------------
// La lista
// ---------------------------------------------------------------------------
class PantallaTraspasos extends ConsumerStatefulWidget {
  const PantallaTraspasos({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaTraspasos> createState() => _EstadoTraspasos();
}

class _EstadoTraspasos extends ConsumerState<PantallaTraspasos> {
  ListaDeTraspasos? _lista;
  String? _error;

  /// El mensaje del último traspaso hecho desde aquí.
  String? _hecho;

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
    try {
      final l = await cliente.traspasos();
      if (!mounted) return;
      setState(() {
        _lista = l;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _nuevo(ListaDeTraspasos lista) async {
    final hecho = await Navigator.of(context).push<TraspasoHecho>(
      MaterialPageRoute(builder: (_) => PantallaNuevoTraspaso(bodegas: lista.bodegas)),
    );
    if (hecho == null || !mounted) return;
    setState(() => _hecho = hecho.mensaje);
    await _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final l = _lista;
    final puedeTraspasar = l != null && l.bodegas.length >= 2 && ref.watch(puedeAjustarProvider);
    return Scaffold(
      key: const Key('pantalla_traspasos'),
      appBar: !widget.conBarra ? null : AppBar(title: const Text('Traspasos entre bodegas')),
      floatingActionButton: !puedeTraspasar
          ? null
          : FloatingActionButton.extended(
              key: const Key('boton_nuevo_traspaso'),
              onPressed: () => _nuevo(l),
              icon: const Icon(Icons.swap_horiz),
              label: const Text('Nuevo traspaso'),
            ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          key: const Key('lista_traspasos'),
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            if (_hecho != null) AvisoDeOficina(_hecho!, key: const Key('aviso_traspaso_hecho')),
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            if (l == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (l != null) ...[
              if (l.bodegas.length < 2)
                const AvisoDeOficina(
                  'Para traspasar se necesitan dos bodegas, y hay una sola. Se dan de '
                  'alta en el panel (Almacenes). A los camiones se les sube con una '
                  'carga, no con un traspaso.',
                  key: Key('aviso_una_bodega'),
                ),
              Text('Recientes', style: Theme.of(context).textTheme.titleMedium),
              if (l.traspasos.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 8),
                  child: Text('Todavía no hay traspasos entre bodegas.'),
                ),
              for (final t in l.traspasos)
                Card(
                  child: ListTile(
                    key: Key('traspaso_${t.folio}'),
                    leading: const Icon(Icons.swap_horiz),
                    title: Text('${t.origen} → ${t.destino}'),
                    subtitle: Text(
                      '${t.folio ?? ''} · ${_cuando(t.creadoEn)}'
                      '\n${t.renglones} producto(s) · ${cantidadLegible(t.piezas)} piezas'
                      '${t.quien == null ? '' : ' · ${t.quien}'}'
                      '${t.nota == null ? '' : '\n${t.nota}'}',
                    ),
                    isThreeLine: true,
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Uno nuevo
// ---------------------------------------------------------------------------
class PantallaNuevoTraspaso extends ConsumerStatefulWidget {
  const PantallaNuevoTraspaso({super.key, required this.bodegas});

  final List<OpcionDeAlmacen> bodegas;

  @override
  ConsumerState<PantallaNuevoTraspaso> createState() => _EstadoNuevoTraspaso();
}

class _EstadoNuevoTraspaso extends ConsumerState<PantallaNuevoTraspaso> {
  late String _origen = widget.bodegas.first.id;
  late String _destino = widget.bodegas[1].id;

  /// Lo que hay en el origen. Se vuelve a pedir al cambiar de origen.
  List<ProductoParaCapturar>? _productos;
  String? _error;
  bool _ocupado = false;
  String _busqueda = '';

  /// Lo tecleado, por producto.
  final Map<String, TextEditingController> _cantidades = {};
  final Map<String, String> _unidades = {};
  final _nota = TextEditingController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  @override
  void dispose() {
    for (final c in _cantidades.values) {
      c.dispose();
    }
    _nota.dispose();
    super.dispose();
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) return;
    setState(() => _productos = null);
    try {
      final p = await cliente.productos(_origen, soloConExistencia: true);
      if (!mounted) return;
      setState(() {
        _productos = p;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  void _cambiarOrigen(String origen) {
    if (origen == _origen) return;
    setState(() {
      _origen = origen;
      if (_destino == origen) {
        _destino = widget.bodegas.firstWhere((b) => b.id != origen).id;
      }
      // Lo tecleado era de la otra bodega.
      for (final c in _cantidades.values) {
        c.clear();
      }
    });
    _cargar();
  }

  String _unidadDe(ProductoParaCapturar p) =>
      _unidades[p.productoId] ??
      (p.presentaciones.isEmpty ? p.unidadBase : p.presentaciones.first.unidad);

  List<RenglonPorTraspasar> _renglones() => [
        for (final p in _productos ?? const <ProductoParaCapturar>[])
          if ((_cantidades[p.productoId]?.text.trim() ?? '').isNotEmpty)
            RenglonPorTraspasar(
              productoId: p.productoId,
              unidad: _unidadDe(p),
              cantidad: _cantidades[p.productoId]!.text.trim(),
            ),
      ];

  String _nombre(String id) => widget.bodegas.firstWhere((b) => b.id == id).nombre;

  Future<void> _traspasar() async {
    final renglones = _renglones();
    if (renglones.isEmpty) {
      setState(() => _error = 'No escribiste ninguna cantidad. Llena los productos que '
          'se van a ${_nombre(_destino)}.');
      return;
    }
    final seguro = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        title: const Text('¿Traspasar?'),
        content: Text(
          '${renglones.length} producto(s) salen de ${_nombre(_origen)} y entran a '
          '${_nombre(_destino)}, en este momento. No se deshace: si te equivocas, '
          'se regresa con otro traspaso.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(contexto).pop(false),
            child: const Text('Todavía no'),
          ),
          FilledButton(
            key: const Key('boton_traspasar_de_verdad'),
            onPressed: () => Navigator.of(contexto).pop(true),
            child: const Text('Traspasar'),
          ),
        ],
      ),
    );
    if (seguro != true || !mounted) return;
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) return;
    setState(() => _ocupado = true);
    try {
      final hecho = await cliente.traspasar(
        origenId: _origen,
        destinoId: _destino,
        renglones: renglones,
        nota: _nota.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).pop(hecho);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _error = explicarErrorDeOficina(e);
        _ocupado = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final productos = _productos;
    final b = _busqueda.trim().toLowerCase();
    return Scaffold(
      key: const Key('pantalla_nuevo_traspaso'),
      appBar: AppBar(title: const Text('Nuevo traspaso')),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
          child: FilledButton.icon(
            key: const Key('boton_traspasar'),
            onPressed: _ocupado || productos == null || productos.isEmpty ? null : _traspasar,
            icon: const Icon(Icons.swap_horiz),
            label: const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text('Traspasar'),
            ),
          ),
        ),
      ),
      body: ListView(
        key: const Key('lista_nuevo_traspaso'),
        padding: const EdgeInsets.all(16),
        children: [
          DropdownButtonFormField<String>(
            key: const Key('campo_origen_traspaso'),
            initialValue: _origen,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Sale de',
              border: OutlineInputBorder(),
            ),
            items: [
              for (final x in widget.bodegas) DropdownMenuItem(value: x.id, child: Text(x.nombre)),
            ],
            onChanged: _ocupado ? null : (o) => _cambiarOrigen(o ?? _origen),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            // La clave cambia con el origen: el campo se reconstruye con el
            // destino que quedó, en vez de conservar uno que ya no está.
            key: Key('campo_destino_traspaso_$_origen'),
            initialValue: _destino,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Entra a',
              border: OutlineInputBorder(),
            ),
            items: [
              for (final x in widget.bodegas)
                if (x.id != _origen) DropdownMenuItem(value: x.id, child: Text(x.nombre)),
            ],
            onChanged: _ocupado ? null : (d) => setState(() => _destino = d ?? _destino),
          ),
          const SizedBox(height: 12),
          TextField(
            key: const Key('campo_nota_traspaso'),
            controller: _nota,
            maxLength: 500,
            decoration: const InputDecoration(
              labelText: 'Nota (opcional)',
              border: OutlineInputBorder(),
            ),
          ),
          if (_error != null)
            AvisoDeOficina(_error!, key: const Key('aviso_traspaso_error'), esError: true),
          if (productos == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (productos != null) ...[
            Text(
              'Lo que hay en ${_nombre(_origen)}',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            if (productos.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 16),
                child: Text(
                  'Esa bodega no tiene existencias: no hay qué traspasar.',
                  key: Key('aviso_origen_vacio'),
                ),
              )
            else ...[
              const SizedBox(height: 8),
              TextField(
                key: const Key('campo_buscar_traspaso'),
                decoration: const InputDecoration(
                  hintText: 'Buscar por nombre o clave',
                  prefixIcon: Icon(Icons.search),
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
                onChanged: (t) => setState(() => _busqueda = t),
              ),
              const SizedBox(height: 8),
              for (final p in productos)
                if (b.isEmpty ||
                    p.nombre.toLowerCase().contains(b) ||
                    p.sku.toLowerCase().contains(b))
                  _renglon(p),
            ],
            const SizedBox(height: 24),
          ],
        ],
      ),
    );
  }

  Widget _renglon(ProductoParaCapturar p) {
    final unidades = [for (final u in p.presentaciones) u.unidad];
    final unidad = _unidadDe(p);
    final bultos = enBultos(p.existencia, p.presentaciones, p.unidadBase);
    return Padding(
      key: Key('traspaso_producto_${p.sku}'),
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p.nombre),
                Text(
                  'Hay ${cantidadLegible(p.existencia)} ${p.unidadBase}'
                  '${bultos == null ? '' : ' ($bultos)'}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            width: 64,
            child: TextField(
              key: Key('cantidad_traspaso_${p.sku}'),
              controller: _cantidades.putIfAbsent(p.productoId, TextEditingController.new),
              keyboardType: TextInputType.number,
              textAlign: TextAlign.center,
              decoration: const InputDecoration(isDense: true, border: OutlineInputBorder()),
            ),
          ),
          const SizedBox(width: 8),
          DropdownButton<String>(
            key: Key('unidad_traspaso_${p.sku}'),
            value: unidades.contains(unidad) ? unidad : null,
            items: [for (final u in unidades) DropdownMenuItem(value: u, child: Text(u))],
            onChanged: (u) => setState(() => _unidades[p.productoId] = u ?? unidad),
          ),
        ],
      ),
    );
  }
}
