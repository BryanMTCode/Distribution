/// La pestaña Almacén del portal de la oficina: todo lo que mueve mercancía.
///
/// Pedido en operación (octubre 2026): «quiero agregar mercancía desde la app y
/// traspasar entre almacenes: manejar todo el negocio en modo gerencia». La
/// pestaña Camiones (cargas y corte) se volvió Almacén, con estas partes:
///
///   · **Existencias**: qué hay en cada bodega y en cada camión.
///   · **Entradas**: la mercancía que llega (compra, inventario inicial, ajuste).
///   · **Traspasos** de una bodega a otra.
///   · **Cargas**: las que piden los vendedores para mañana, para aceptarlas, y
///     subirle mercancía al camión a mano.
///   · **Corte del día**: los cortes que mandan los vendedores, para cerrarlos,
///     y el corte contando el camión.
///
/// La carga y el corte van separados a propósito (ADR 0002 §82): primero se
/// cierra el corte del vendedor y después se acepta su carga.
///
/// Cada parte aparece solo con su permiso; con una sola, se muestra directo,
/// sin pestañas. La UI oculta; el servidor prohíbe.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/almacen.dart';
import '../../estado/cargas.dart';
import '../../estado/vendedores.dart';
import 'cargas.dart';
import 'comunes.dart';
import 'cortes.dart';
import 'entradas.dart';
import 'traspasos.dart';

/// Si la pestaña Almacén aparece en la barra.
final muestraAlmacenProvider = Provider<bool>(
  (ref) =>
      ref.watch(puedeVerAlmacenProvider) ||
      ref.watch(puedeCargarProvider) ||
      ref.watch(puedeCortarProvider),
);

class PantallaAlmacen extends ConsumerWidget {
  const PantallaAlmacen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ver = ref.watch(puedeVerAlmacenProvider);
    // (clave, título, pantalla con o sin barra propia)
    final partes = <(Key, String, Widget Function(bool conBarra))>[
      if (ver) ...[
        (
          const Key('pestana_existencias'),
          'Existencias',
          (b) => PantallaExistencias(conBarra: b),
        ),
        (const Key('pestana_entradas'), 'Entradas', (b) => PantallaEntradas(conBarra: b)),
        (const Key('pestana_traspasos'), 'Traspasos', (b) => PantallaTraspasos(conBarra: b)),
      ],
      if (ref.watch(puedeCargarProvider))
        (const Key('pestana_cargas'), 'Cargas', (b) => PantallaCargas(conBarra: b)),
      if (ref.watch(puedeCortarProvider))
        (const Key('pestana_cortes'), 'Corte del día', (b) => PantallaCortes(conBarra: b)),
    ];
    if (partes.isEmpty) return const SizedBox.shrink();
    if (partes.length == 1) return partes.single.$3(true);
    return DefaultTabController(
      length: partes.length,
      child: Scaffold(
        key: const Key('pantalla_almacen'),
        appBar: AppBar(
          title: const Text('Almacén'),
          bottom: TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            tabs: [for (final p in partes) Tab(key: p.$1, text: p.$2)],
          ),
        ),
        body: TabBarView(children: [for (final p in partes) p.$3(false)]),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Piezas comunes de las pantallas del almacén
// ---------------------------------------------------------------------------

/// «760» piezas con caja de 24 → «31 CAJA + 16 PZA»: así se cuenta en el
/// anaquel. Nulo cuando no hay presentación más grande que la pieza, o cuando
/// no llega ni a un bulto.
String? enBultos(String cantidad, List<PresentacionDeCarga> presentaciones, String unidadBase) {
  final total = double.parse(cantidad);
  if (total <= 0) return null;
  PresentacionDeCarga? grande;
  for (final p in presentaciones) {
    if (double.parse(p.factor) > 1) {
      grande = p;
      break;
    }
  }
  if (grande == null) return null;
  final factor = double.parse(grande.factor);
  final bultos = (total / factor).floor();
  if (bultos == 0) return null;
  final resto = cantidadLegible((total - bultos * factor).toStringAsFixed(3));
  return resto == '0'
      ? '$bultos ${grande.unidad}'
      : '$bultos ${grande.unidad} + $resto $unidadBase';
}

/// Elegir un producto de una lista larga, buscando por nombre o clave.
Future<ProductoParaCapturar?> elegirProducto(
  BuildContext context,
  List<ProductoParaCapturar> productos, {
  required String titulo,
}) =>
    Navigator.of(context).push<ProductoParaCapturar>(
      MaterialPageRoute(builder: (_) => _ElegirProducto(productos: productos, titulo: titulo)),
    );

class _ElegirProducto extends StatefulWidget {
  const _ElegirProducto({required this.productos, required this.titulo});

  final List<ProductoParaCapturar> productos;
  final String titulo;

  @override
  State<_ElegirProducto> createState() => _EstadoElegirProducto();
}

class _EstadoElegirProducto extends State<_ElegirProducto> {
  String _busqueda = '';

  @override
  Widget build(BuildContext context) {
    final b = _busqueda.trim().toLowerCase();
    final visibles = [
      for (final p in widget.productos)
        if (b.isEmpty || p.nombre.toLowerCase().contains(b) || p.sku.toLowerCase().contains(b))
          p,
    ];
    return Scaffold(
      key: const Key('pantalla_elegir_producto'),
      appBar: AppBar(title: Text(widget.titulo)),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              key: const Key('campo_buscar_producto'),
              autofocus: true,
              decoration: const InputDecoration(
                hintText: 'Buscar por nombre o clave',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
                isDense: true,
              ),
              onChanged: (t) => setState(() => _busqueda = t),
            ),
          ),
          Expanded(
            child: visibles.isEmpty
                ? const Center(child: Text('Ningún producto con ese nombre.'))
                : ListView.builder(
                    itemCount: visibles.length,
                    itemBuilder: (_, i) {
                      final p = visibles[i];
                      return ListTile(
                        key: Key('elegir_${p.sku}'),
                        title: Text(p.nombre),
                        subtitle: Text(
                          '${p.sku} · hay ${cantidadLegible(p.existencia)} ${p.unidadBase}',
                        ),
                        onTap: () => Navigator.of(context).pop(p),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Existencias
// ---------------------------------------------------------------------------
class PantallaExistencias extends ConsumerStatefulWidget {
  const PantallaExistencias({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaExistencias> createState() => _EstadoExistencias();
}

class _EstadoExistencias extends ConsumerState<PantallaExistencias> {
  List<AlmacenResumen>? _almacenes;
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
    try {
      final a = await cliente.almacenes();
      if (!mounted) return;
      setState(() {
        _almacenes = a;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final a = _almacenes;
    final estilo = Theme.of(context).textTheme;
    final bodegas = [for (final x in a ?? const <AlmacenResumen>[]) if (x.esBodega) x];
    final camiones = [for (final x in a ?? const <AlmacenResumen>[]) if (!x.esBodega) x];
    final cuerpo = RefreshIndicator(
      onRefresh: _cargar,
      child: ListView(
        key: const Key('lista_almacenes'),
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) AvisoDeOficina(_error!, esError: true),
          if (a == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (a != null) ...[
            Text('Bodegas', style: estilo.titleMedium),
            if (bodegas.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('No hay bodegas dadas de alta. Se dan de alta en el panel.'),
              ),
            for (final x in bodegas) _tarjeta(x),
            const SizedBox(height: 16),
            Text('Camiones', style: estilo.titleMedium),
            if (camiones.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('No hay camiones dados de alta.'),
              ),
            for (final x in camiones) _tarjeta(x),
          ],
        ],
      ),
    );
    if (!widget.conBarra) return cuerpo;
    return Scaffold(
      key: const Key('pantalla_existencias'),
      appBar: AppBar(title: const Text('Existencias')),
      body: cuerpo,
    );
  }

  Widget _tarjeta(AlmacenResumen x) {
    final error = Theme.of(context).colorScheme.error;
    return Card(
      child: ListTile(
        key: Key('almacen_${x.codigo}'),
        leading: Icon(x.esBodega ? Icons.warehouse_outlined : Icons.local_shipping_outlined),
        title: Text(x.nombre),
        subtitle: Text.rich(
          TextSpan(
            children: [
              TextSpan(
                text: '${x.productos} producto(s) · ${cantidadLegible(x.piezas)} piezas'
                    ' · ${pesos(x.valor)}'
                    '${x.responsable == null ? '' : '\n${x.responsable}'}',
              ),
              if (x.negativos > 0)
                TextSpan(
                  text: '\n${x.negativos} en negativo',
                  style: TextStyle(color: error, fontWeight: FontWeight.w600),
                ),
            ],
          ),
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => PantallaExistenciasDelAlmacen(almacen: x)),
        ),
      ),
    );
  }
}

/// Lo que hay en un almacén, producto por producto.
class PantallaExistenciasDelAlmacen extends ConsumerStatefulWidget {
  const PantallaExistenciasDelAlmacen({super.key, required this.almacen});

  final AlmacenResumen almacen;

  @override
  ConsumerState<PantallaExistenciasDelAlmacen> createState() => _EstadoDelAlmacen();
}

class _EstadoDelAlmacen extends ConsumerState<PantallaExistenciasDelAlmacen> {
  ExistenciasDelAlmacen? _datos;
  String? _error;
  String _busqueda = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteAlmacenProvider);
    if (cliente == null) return;
    try {
      final d = await cliente.existencias(widget.almacen.id);
      if (!mounted) return;
      setState(() {
        _datos = d;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _datos;
    final almacen = d?.almacen ?? widget.almacen;
    final colores = Theme.of(context).colorScheme;
    final b = _busqueda.trim().toLowerCase();
    final visibles = [
      for (final e in d?.existencias ?? const <ExistenciaDeProducto>[])
        if (b.isEmpty || e.nombre.toLowerCase().contains(b) || e.sku.toLowerCase().contains(b))
          e,
    ];
    return Scaffold(
      key: const Key('pantalla_existencias_almacen'),
      appBar: AppBar(title: Text(almacen.nombre)),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(
              '${almacen.productos} producto(s) · ${cantidadLegible(almacen.piezas)} piezas'
              '${almacen.responsable == null ? '' : ' · ${almacen.responsable}'}',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            // Lo que vale, como en la hoja de la dirección: existencia × precio de
            // venta. Lo negativo y lo que no tiene precio no suman.
            Text(
              'Vale ${pesos(almacen.valor)} a precio de venta',
              key: const Key('valor_del_almacen'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if (d != null && d.existencias.any((e) => e.precio == null))
              Text(
                '${d.existencias.where((e) => e.precio == null).length} artículo(s) sin '
                'precio no suman: pónselo en el panel, en Productos.',
                style: TextStyle(color: colores.error),
              ),
            const SizedBox(height: 8),
            // §0.3: lo del camión es un piso. Lo que vendió sin señal no está.
            if (!almacen.esBodega)
              const AvisoDeOficina(
                'Lo que el sistema sabe del camión hasta la última vez que subió el '
                'teléfono del vendedor. Lo vendido sin señal todavía no se descuenta.',
              ),
            if (almacen.negativos > 0)
              AvisoDeOficina(
                '${almacen.negativos} producto(s) en negativo: salió algo que el sistema '
                'no tenía. Hay que contarlo y capturar la entrada por conteo.',
                key: const Key('aviso_negativos'),
                esError: true,
              ),
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            if (d == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (d != null) ...[
              TextField(
                key: const Key('campo_buscar_existencia'),
                decoration: const InputDecoration(
                  hintText: 'Buscar por nombre o clave',
                  prefixIcon: Icon(Icons.search),
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
                onChanged: (t) => setState(() => _busqueda = t),
              ),
              const SizedBox(height: 8),
              if (d.existencias.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Text('Este almacén no tiene existencias.'),
                ),
              for (final e in visibles)
                ListTile(
                  key: Key('existencia_${e.sku}'),
                  contentPadding: EdgeInsets.zero,
                  title: Text(e.nombre),
                  subtitle: Text(
                    [
                      e.sku,
                      enBultos(e.cantidad, e.presentaciones, e.unidadBase),
                      e.precio == null ? 'sin precio' : '${pesos(e.precio!)} c/u',
                    ].whereType<String>().join(' · '),
                  ),
                  trailing: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        '${cantidadLegible(e.cantidad)} ${e.unidadBase}',
                        style: TextStyle(
                          fontWeight: FontWeight.w600,
                          color: e.negativa ? colores.error : null,
                        ),
                      ),
                      Text(
                        e.valor == null ? '—' : pesos(e.valor!),
                        key: Key('valor_${e.sku}'),
                        style: TextStyle(color: e.negativa ? colores.error : null),
                      ),
                    ],
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
