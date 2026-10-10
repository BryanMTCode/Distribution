/// Los artículos activos, por familia: lo que abre «artículos activos» en
/// Empresa (ADR 0002 §97).
///
/// Pedido de la dirección (octubre 2026): «si cliqueo en 25 artículos activos,
/// que me lleve a la lista de artículos». Es de consulta: precio y lo que hay en
/// bodegas y camiones. Los artículos se editan en el panel.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/almacen.dart';
import '../../estado/vendedores.dart';
import '../familias.dart';
import 'comunes.dart';

class PantallaArticulos extends ConsumerStatefulWidget {
  const PantallaArticulos({super.key, this.soloSinPrecio = false});

  /// Desde «N sin precio»: solo esos, que son los que hay que arreglar.
  final bool soloSinPrecio;

  @override
  ConsumerState<PantallaArticulos> createState() => _EstadoArticulos();
}

String? _familiaDe(ArticuloDelCatalogo a) => a.familia;

class _EstadoArticulos extends ConsumerState<PantallaArticulos>
    with FamiliasPlegables<PantallaArticulos> {
  List<ArticuloDelCatalogo>? _articulos;
  String? _error;
  String _busqueda = '';

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
      final a = await cliente.articulos();
      if (!mounted) return;
      setState(() {
        _articulos = a;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final b = _busqueda.trim().toLowerCase();
    final visibles = [
      for (final a in _articulos ?? const <ArticuloDelCatalogo>[])
        if ((!widget.soloSinPrecio || a.precio == null) &&
            (b.isEmpty || a.nombre.toLowerCase().contains(b) || a.sku.toLowerCase().contains(b)))
          a,
    ];
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_articulos'),
      appBar: AppBar(
        title: Text(widget.soloSinPrecio ? 'Artículos sin precio' : 'Artículos'),
        actions: [
          if (hayFamilias(visibles, _familiaDe))
            BotonPlegarFamilias(
              algunaPlegada: plegadas.isNotEmpty,
              alTocar: () => alternarTodas(visibles, _familiaDe),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_error != null) AvisoDeOficina(_error!, esError: true),
            if (_articulos == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (_articulos != null) ...[
              Text(
                '${visibles.length} artículo(s)'
                '${widget.soloSinPrecio ? ' sin precio: pónselo en el panel, en Productos' : ''}',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              const SizedBox(height: 8),
              TextField(
                key: const Key('campo_buscar_articulo'),
                decoration: const InputDecoration(
                  hintText: 'Buscar por nombre o clave',
                  prefixIcon: Icon(Icons.search),
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
                onChanged: (t) => setState(() => _busqueda = t),
              ),
              const SizedBox(height: 8),
              for (final entrada in entradasPorFamilia(
                visibles,
                _familiaDe,
                plegadas: plegadas,
                todoAbierto: b.isNotEmpty,
              ))
                switch (entrada) {
                  EncabezadoEntrada(:final grupo, :final abierta) => Padding(
                      padding: const EdgeInsets.only(top: 12, bottom: 2),
                      child: EncabezadoDeFamilia(
                        familia: grupo.familia,
                        cuantos: grupo.elementos.length,
                        abierta: abierta,
                        alTocar: () => alternarFamilia(grupo.familia),
                      ),
                    ),
                  ElementoEntrada(elemento: final a) => ListTile(
                      key: Key('articulo_${a.sku}'),
                      contentPadding: EdgeInsets.zero,
                      title: Text(a.nombre),
                      subtitle: Text(
                        '${a.sku} · '
                        '${a.precio == null ? 'sin precio' : '${pesos(a.precio!)} c/u'}',
                        style: a.precio == null ? TextStyle(color: colores.error) : null,
                      ),
                      trailing: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text('${cantidadLegible(a.enBodegas)} en bodega'),
                          Text(
                            '${cantidadLegible(a.enCamiones)} en camiones',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ],
                      ),
                    ),
                },
            ],
          ],
        ),
      ),
    );
  }
}
