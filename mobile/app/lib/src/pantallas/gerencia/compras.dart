/// Recibir una compra de proveedor desde el teléfono, aunque no haya señal.
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ ES (ADR 0002 §83)
/// ─────────────────────────────────────────────────────────────────────────
/// «Agregar producto» aquí NO es dar de alta un artículo: es registrar que llegó
/// mercancía del proveedor —qué productos y cuántos— para que sume a la bodega
/// principal. El gerente lo hace donde la recibe, en la calle, y ahí a veces no
/// hay señal: la compra se guarda en el teléfono y se manda sola al tenerla.
///
/// El costo es opcional por renglón: con costo mueve el promedio y entra a la
/// cuenta por pagar del proveedor; sin costo solo suma inventario.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/almacen.dart';
import '../../estado/sesion.dart';
import 'comunes.dart';

/// Manda lo que haya en la cola. Sin señal no hace nada ni avisa: la compra
/// sigue guardada y se manda la siguiente vez. Con la cola vacía no pregunta
/// nada al servidor.
Future<ResultadoDeEnvio?> enviarComprasPendientes(WidgetRef ref) async {
  final cliente = ref.read(clienteAlmacenProvider);
  if (cliente == null) return null;
  final compras = ref.read(comprasSinSenalProvider);
  if (compras.pendientes == 0) return const ResultadoDeEnvio();
  try {
    final r = await compras.enviar(cliente);
    ref.read(revisionDeComprasProvider.notifier).state++;
    return r;
  } on Object {
    return null;
  }
}

/// Refresca la copia del catálogo si es vieja: es lo que deja capturar una
/// compra sin señal. Se hace al abrir Entradas con señal, sin estorbar.
Future<void> refrescarCatalogoDeCompras(WidgetRef ref, {Duration vigencia = const Duration(hours: 6)}) async {
  final cliente = ref.read(clienteAlmacenProvider);
  if (cliente == null) return;
  final compras = ref.read(comprasSinSenalProvider);
  final copia = compras.catalogoGuardado();
  final ahora = ref.read(relojProvider)();
  if (copia != null && ahora.difference(copia.$2) < vigencia) return;
  try {
    compras.guardarCatalogo(await cliente.catalogoDeCompras());
  } on Object {
    // Es un extra: sin él, «Recibir compra» lo baja al abrirse.
  }
}

// ===========================================================================
// La tarjeta de la lista de entradas
// ===========================================================================

/// Lo capturado en este teléfono: lo que falta mandar, lo rechazado y lo último
/// que entró.
class TarjetaComprasDelTelefono extends ConsumerWidget {
  const TarjetaComprasDelTelefono({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.watch(revisionDeComprasProvider);
    final compras = ref.watch(comprasSinSenalProvider);
    final todas = compras.todas(limite: 10);
    final colores = Theme.of(context).colorScheme;
    final porMandar = todas.where((c) => c.pendiente).length;

    return Card(
      key: const Key('tarjeta_compras_telefono'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Compras de proveedor', style: Theme.of(context).textTheme.titleMedium),
            const Text('Recibe lo que llega del proveedor aunque no haya señal: se '
                'guarda en el teléfono y entra a la bodega al tenerla.'),
            const SizedBox(height: 8),
            if (ref.watch(puedeAjustarProvider))
              FilledButton.icon(
                key: const Key('boton_recibir_compra'),
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const PantallaRecibirCompra()),
                ),
                icon: const Icon(Icons.add_shopping_cart),
                label: const Text('Recibir compra'),
              ),
            if (porMandar > 0) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      '$porMandar compra(s) por mandar',
                      key: const Key('compras_por_mandar'),
                      style: TextStyle(color: colores.error, fontWeight: FontWeight.w600),
                    ),
                  ),
                  TextButton.icon(
                    key: const Key('boton_enviar_compras'),
                    onPressed: () async {
                      final r = await enviarComprasPendientes(ref);
                      if (!context.mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                        content: Text(r == null || r.sinSenal
                            ? 'Sin señal: siguen guardadas y se mandan después.'
                            : '${r.enviadas} enviada(s)'
                                '${r.rechazadas > 0 ? ', ${r.rechazadas} rechazada(s)' : ''}.'),
                      ));
                    },
                    icon: const Icon(Icons.cloud_upload_outlined),
                    label: const Text('Mandar ahora'),
                  ),
                ],
              ),
            ],
            for (final c in todas)
              ListTile(
                key: Key('compra_${c.compra.id}'),
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  c.pendiente
                      ? Icons.cloud_off_outlined
                      : c.rechazada
                          ? Icons.error_outline
                          : Icons.cloud_done_outlined,
                  color: c.rechazada ? colores.error : null,
                ),
                title: Text(
                  '${c.compra.proveedor.isEmpty ? 'Compra' : c.compra.proveedor} · '
                  '${c.compra.renglones.length} producto(s)',
                ),
                subtitle: Text(
                  c.pendiente
                      ? 'Por mandar · ${diaEnPalabras(c.compra.fecha)}'
                      : c.rechazada
                          ? 'No entró: ${c.mensaje ?? ''}'
                          : 'Entró: ${c.folio ?? ''}',
                ),
                trailing: c.rechazada
                    ? IconButton(
                        key: Key('reintentar_compra_${c.compra.id}'),
                        tooltip: 'Volver a intentar',
                        icon: const Icon(Icons.refresh),
                        onPressed: () {
                          compras.reintentar(c.compra.id);
                          ref.read(revisionDeComprasProvider.notifier).state++;
                        },
                      )
                    : null,
              ),
          ],
        ),
      ),
    );
  }
}

// ===========================================================================
// Recibir la compra
// ===========================================================================

class _Renglon {
  _Renglon(this.producto) : presentacion = producto.porOmision;

  final ProductoParaComprar producto;
  (String, Factor) presentacion;
  final bultos = TextEditingController();
  final costo = TextEditingController();

  void liberar() {
    bultos.dispose();
    costo.dispose();
  }
}

class PantallaRecibirCompra extends ConsumerStatefulWidget {
  const PantallaRecibirCompra({super.key});

  @override
  ConsumerState<PantallaRecibirCompra> createState() => _EstadoRecibir();
}

class _EstadoRecibir extends ConsumerState<PantallaRecibirCompra> {
  CatalogoDeCompras? _catalogo;
  DateTime? _catalogoDe;
  bool _deLaCopia = false;
  bool _cargando = true;
  String? _proveedorId;
  final _proveedor = TextEditingController();
  final _referencia = TextEditingController();
  final _nota = TextEditingController();
  final List<_Renglon> _renglones = [];
  String? _error;
  bool _guardando = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargarCatalogo());
  }

  @override
  void dispose() {
    _proveedor.dispose();
    _referencia.dispose();
    _nota.dispose();
    for (final r in _renglones) {
      r.liberar();
    }
    super.dispose();
  }

  /// Con señal, el catálogo del servidor (y se guarda); sin ella, la copia.
  Future<void> _cargarCatalogo() async {
    final compras = ref.read(comprasSinSenalProvider);
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente != null) {
      try {
        final c = await cliente.catalogoDeCompras();
        compras.guardarCatalogo(c);
        if (!mounted) return;
        setState(() {
          _catalogo = c;
          _deLaCopia = false;
          _cargando = false;
        });
        return;
      } on Object {
        // Sin señal: la copia.
      }
    }
    final copia = compras.catalogoGuardado();
    if (!mounted) return;
    setState(() {
      _catalogo = copia?.$1;
      _catalogoDe = copia?.$2;
      _deLaCopia = true;
      _cargando = false;
    });
  }

  Future<void> _agregar() async {
    final catalogo = _catalogo;
    if (catalogo == null) return;
    final elegido = await Navigator.of(context).push<ProductoParaComprar>(
      MaterialPageRoute(builder: (_) => _ElegirProducto(productos: catalogo.productos)),
    );
    if (elegido == null || !mounted) return;
    setState(() {
      if (_renglones.any((r) => r.producto.productoId == elegido.productoId)) return;
      _renglones.add(_Renglon(elegido));
    });
  }

  Future<void> _guardar() async {
    final renglones = <RenglonDeCompra>[];
    for (final r in _renglones) {
      final bultos = int.tryParse(r.bultos.text.trim()) ?? 0;
      if (bultos <= 0) continue;
      final textoCosto = r.costo.text.trim().replaceAll(',', '').replaceAll(r'$', '');
      Dinero? costo;
      if (textoCosto.isNotEmpty) {
        final m = RegExp(r'^(\d{1,9})(?:\.(\d{1,2}))?$').firstMatch(textoCosto);
        if (m == null) {
          setState(() => _error = '${r.producto.nombre}: «${r.costo.text}» no es un costo.');
          return;
        }
        costo = Dinero.deTexto('${m.group(1)}.${(m.group(2) ?? '').padRight(2, '0')}');
      }
      renglones.add(RenglonDeCompra(
        productoId: r.producto.productoId,
        nombre: r.producto.nombre,
        unidad: r.presentacion.$1,
        factor: r.presentacion.$2,
        bultos: bultos,
        costoPorBulto: costo,
      ));
    }
    final compras = ref.read(comprasSinSenalProvider);
    final proveedorDelCatalogo = _catalogo?.proveedores
        .where((p) => p.id == _proveedorId)
        .firstOrNull;
    try {
      compras.capturar(
        renglones: renglones,
        proveedorId: proveedorDelCatalogo?.id,
        proveedor: proveedorDelCatalogo?.nombre ?? _proveedor.text,
        referencia: _referencia.text,
        nota: _nota.text,
      );
    } on MotivoNoCompra catch (e) {
      setState(() => _error = e.mensaje);
      return;
    }
    setState(() {
      _guardando = true;
      _error = null;
    });
    final r = await enviarComprasPendientes(ref);
    if (!mounted) return;
    final mensaje = r == null || r.sinSenal
        ? 'Compra guardada en el teléfono. Entra a la bodega en cuanto haya señal.'
        : r.rechazadas > 0
            ? 'El servidor no la aceptó: ${r.ultimoMensaje ?? ''}'
            : (r.ultimoMensaje ?? 'Compra recibida en la bodega.');
    final messenger = ScaffoldMessenger.of(context);
    Navigator.of(context).pop();
    messenger.showSnackBar(SnackBar(content: Text(mensaje), duration: const Duration(seconds: 6)));
  }

  @override
  Widget build(BuildContext context) {
    final catalogo = _catalogo;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_recibir_compra'),
      appBar: AppBar(title: const Text('Recibir compra')),
      body: _cargando
          ? const Center(child: CircularProgressIndicator())
          : catalogo == null
              ? const Padding(
                  padding: EdgeInsets.all(16),
                  child: AvisoDeOficina(
                    'Para capturar sin señal hace falta haber tenido señal una vez '
                    'en esta pantalla: así el teléfono guarda el catálogo. Busca '
                    'señal y vuelve a entrar.',
                    esError: true,
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    if (_deLaCopia)
                      AvisoDeOficina(
                        'Sin señal: se usa el catálogo guardado'
                        '${_catalogoDe == null ? '' : ' (${diaEnPalabras(diaOperativoDe(_catalogoDe!))})'}. '
                        'La compra se guarda y se manda sola al tener señal.',
                      ),
                    const Text('Entra a la bodega principal. «Agregar producto» es lo '
                        'que llegó del proveedor, no un artículo nuevo del catálogo.'),
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String?>(
                      key: const Key('campo_proveedor_compra'),
                      initialValue: _proveedorId,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Proveedor',
                        border: OutlineInputBorder(),
                      ),
                      items: [
                        const DropdownMenuItem(value: null, child: Text('Otro (escribirlo)')),
                        for (final p in catalogo.proveedores)
                          DropdownMenuItem(value: p.id, child: Text(p.nombre)),
                      ],
                      onChanged: (v) => setState(() => _proveedorId = v),
                    ),
                    if (_proveedorId == null) ...[
                      const SizedBox(height: 8),
                      TextField(
                        key: const Key('campo_proveedor_libre'),
                        controller: _proveedor,
                        decoration: const InputDecoration(
                          labelText: '¿De quién? (sin cuenta por pagar)',
                          border: OutlineInputBorder(),
                        ),
                      ),
                    ],
                    const SizedBox(height: 8),
                    TextField(
                      key: const Key('campo_referencia_compra'),
                      controller: _referencia,
                      decoration: const InputDecoration(
                        labelText: 'Remisión o factura (opcional)',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 16),
                    Text('Lo que llegó', style: estilo.titleMedium),
                    if (_renglones.isEmpty)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 8),
                        child: Text('Todavía nada.'),
                      ),
                    for (final r in _renglones) _renglon(r, colores),
                    OutlinedButton.icon(
                      key: const Key('boton_agregar_producto_compra'),
                      onPressed: _agregar,
                      icon: const Icon(Icons.add),
                      label: const Text('Agregar producto'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('campo_nota_compra'),
                      controller: _nota,
                      maxLength: 500,
                      decoration: const InputDecoration(
                        labelText: 'Nota (opcional)',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    if (_error != null)
                      Text(_error!, key: const Key('error_compra'),
                          style: TextStyle(color: colores.error)),
                    const SizedBox(height: 8),
                    FilledButton.icon(
                      key: const Key('boton_guardar_compra'),
                      onPressed: _guardando ? null : _guardar,
                      icon: const Icon(Icons.save_alt),
                      label: const Text('Guardar compra'),
                    ),
                  ],
                ),
    );
  }

  Widget _renglon(_Renglon r, ColorScheme colores) => Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(child: Text(r.producto.nombre)),
                  IconButton(
                    tooltip: 'Quitar',
                    icon: const Icon(Icons.close),
                    onPressed: () => setState(() {
                      _renglones.remove(r);
                      r.liberar();
                    }),
                  ),
                ],
              ),
              Row(
                children: [
                  SizedBox(
                    width: 80,
                    child: TextField(
                      key: Key('bultos_${r.producto.sku}'),
                      controller: r.bultos,
                      keyboardType: TextInputType.number,
                      inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                      decoration: const InputDecoration(
                        labelText: 'Cuántos',
                        isDense: true,
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  if (r.producto.presentaciones.length > 1)
                    DropdownButton<String>(
                      value: r.presentacion.$1,
                      items: [
                        for (final (u, f) in r.producto.presentaciones)
                          DropdownMenuItem(
                            value: u,
                            child: Text(f.esUno ? u : '$u ${f.textoCorto}'),
                          ),
                      ],
                      onChanged: (u) => setState(() => r.presentacion =
                          r.producto.presentaciones.firstWhere((p) => p.$1 == u)),
                    )
                  else
                    Text(r.presentacion.$1),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      key: Key('costo_${r.producto.sku}'),
                      controller: r.costo,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: InputDecoration(
                        labelText: 'Costo por ${r.presentacion.$1.toLowerCase()}',
                        hintText: 'opcional',
                        prefixText: r'$ ',
                        isDense: true,
                        border: const OutlineInputBorder(),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
}

class _ElegirProducto extends StatefulWidget {
  const _ElegirProducto({required this.productos});

  final List<ProductoParaComprar> productos;

  @override
  State<_ElegirProducto> createState() => _EstadoElegir();
}

class _EstadoElegir extends State<_ElegirProducto> {
  String _busqueda = '';

  @override
  Widget build(BuildContext context) {
    final q = _busqueda.trim().toLowerCase();
    final lista = widget.productos
        .where((p) => q.isEmpty || p.nombre.toLowerCase().contains(q) ||
            p.sku.toLowerCase().contains(q))
        .take(200)
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('¿Qué llegó?')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              key: const Key('buscar_producto_compra'),
              autofocus: true,
              onChanged: (t) => setState(() => _busqueda = t),
              decoration: const InputDecoration(
                hintText: 'Nombre o clave',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
              ),
            ),
          ),
          Expanded(
            child: ListView(
              children: [
                for (final p in lista)
                  ListTile(
                    key: Key('producto_compra_${p.sku}'),
                    title: Text(p.nombre),
                    subtitle: Text(p.sku),
                    onTap: () => Navigator.of(context).pop(p),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
