/// Los clientes, desde el teléfono de la oficina.
///
/// Pedido en operación (octubre 2026): «agrega lo de los clientes en la app del
/// gerente». Todo es de contado (ADR 0002 §81), así que lo que la oficina
/// pregunta de una tienda ya no es cuánto debe:
///
///   · ¿Dónde está? Los que no tienen ubicación se filtran aparte: sin ella el
///     vendedor no tiene geocerca y el mapa no los dibuja.
///   · ¿Qué compra y cómo paga? Sus ventas —cada una se abre con lo que se le
///     vendió— con su forma de pago.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import 'comunes.dart';
import 'vendedores.dart';

ClienteClientesDeOficina? _cliente(WidgetRef ref) {
  final t = ref.read(transporteProvider);
  return t == null ? null : ClienteClientesDeOficina(t);
}

const _filtros = [
  ('todos', 'Todos'),
  ('prospectos', 'Prospectos'),
  ('sin_ubicacion', 'Sin ubicación'),
];

class PantallaClientesDeOficina extends ConsumerStatefulWidget {
  const PantallaClientesDeOficina({super.key});

  @override
  ConsumerState<PantallaClientesDeOficina> createState() => _EstadoClientes();
}

class _EstadoClientes extends ConsumerState<PantallaClientesDeOficina> {
  String _filtro = 'todos';
  final _busqueda = TextEditingController();
  ListaDeClientesDeOficina? _lista;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  @override
  void dispose() {
    _busqueda.dispose();
    super.dispose();
  }

  Future<void> _cargar() async {
    final cliente = _cliente(ref);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final l = await cliente.lista(filtro: _filtro, q: _busqueda.text);
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

  @override
  Widget build(BuildContext context) {
    final l = _lista;
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_clientes_oficina'),
      appBar: AppBar(title: const Text('Clientes')),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              key: const Key('buscar_cliente_oficina'),
              controller: _busqueda,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => _cargar(),
              decoration: InputDecoration(
                hintText: 'Nombre, código o teléfono',
                prefixIcon: const Icon(Icons.search),
                border: const OutlineInputBorder(),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.arrow_forward),
                  onPressed: _cargar,
                ),
              ),
            ),
            const SizedBox(height: 8),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  for (final (clave, etiqueta) in _filtros)
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        key: Key('filtro_cliente_$clave'),
                        label: Text(
                          l?.conteos[clave] == null ? etiqueta : '$etiqueta (${l!.conteos[clave]})',
                        ),
                        selected: _filtro == clave,
                        onSelected: (_) {
                          setState(() => _filtro = clave);
                          _cargar();
                        },
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 8),
            if (_error != null) Text(_error!, style: TextStyle(color: colores.error)),
            if (l == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (l != null && l.clientes.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Text('No hay clientes con ese filtro.', textAlign: TextAlign.center),
              ),
            for (final c in l?.clientes ?? const <ClienteDeOficina>[])
              Card(
                child: ListTile(
                  key: Key('cliente_oficina_${c.codigo ?? c.id}'),
                  leading: Icon(
                    c.conUbicacion ? Icons.storefront_outlined : Icons.location_off_outlined,
                    color: c.conUbicacion ? null : colores.error,
                  ),
                  title: Text(c.nombre),
                  subtitle: Text(
                    [
                      c.ruta ?? 'sin ruta',
                      if (c.estatus == 'prospecto') 'prospecto',
                      if (!c.conUbicacion) 'sin ubicación',
                      c.ultimaCompra == null
                          ? 'nunca ha comprado'
                          : 'última compra: ${diaEnPalabras(c.ultimaCompra!)}',
                    ].join(' · '),
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (_) => PantallaFichaDeCliente(clienteId: c.id, nombre: c.nombre),
                      ),
                    );
                    await _cargar();
                  },
                ),
              ),
            if (l != null && l.recortado)
              const Text('Se muestran los primeros 300. Busca por nombre para ver el resto.'),
          ],
        ),
      ),
    );
  }
}

class PantallaFichaDeCliente extends ConsumerStatefulWidget {
  const PantallaFichaDeCliente({super.key, required this.clienteId, required this.nombre});

  final String clienteId;
  final String nombre;

  @override
  ConsumerState<PantallaFichaDeCliente> createState() => _EstadoFicha();
}

class _EstadoFicha extends ConsumerState<PantallaFichaDeCliente> {
  FichaDelCliente? _f;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _hacer((c) => c.ficha(widget.clienteId)));
  }

  Future<void> _hacer(Future<FichaDelCliente> Function(ClienteClientesDeOficina) accion) async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    try {
      final f = await accion(cliente);
      if (!mounted) return;
      setState(() {
        _f = f;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final f = _f;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_ficha_cliente'),
      appBar: AppBar(title: Text(widget.nombre)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) Text(_error!, style: TextStyle(color: colores.error)),
          if (f == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (f != null) ...[
            if (f.mensaje != null)
              Container(
                key: const Key('aviso_ficha_cliente'),
                width: double.infinity,
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: colores.secondaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(f.mensaje!),
              ),
            Text(
              [f.codigo, f.ruta, if (f.estatus != 'activo') f.estatus].whereType<String>().join(' · '),
              style: estilo.titleSmall,
            ),
            if (f.contacto != null || f.telefono != null)
              Text([f.contacto, f.telefono].whereType<String>().join(' · ')),
            if (f.direccion != null) Text(f.direccion!),
            const SizedBox(height: 12),
            Card(
              key: const Key('tarjeta_cliente_compras'),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Compró ${pesos(f.compradoMes)} este mes', style: estilo.titleMedium),
                    Text('${pesos(f.compradoAnio)} este año', style: estilo.bodySmall),
                    const SizedBox(height: 8),
                    Text(
                      f.conUbicacion
                          ? 'Ubicación: ${f.lat!.toStringAsFixed(6)}, ${f.lng!.toStringAsFixed(6)}'
                              '${f.ubicacionOrigen == 'gps' ? ' (GPS)' : f.ubicacionOrigen == 'manual' ? ' (a mano)' : ''}'
                          : 'Sin ubicación: el vendedor no tiene geocerca para esta tienda.',
                      key: const Key('ubicacion_cliente_oficina'),
                      style: TextStyle(color: f.conUbicacion ? null : colores.error),
                    ),
                    if (f.referencias != null) Text(f.referencias!, style: estilo.bodySmall),
                  ],
                ),
              ),
            ),
            const Divider(height: 32),
            Text('Sus compras', style: estilo.titleMedium),
            if (f.ventas.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('No ha comprado.'),
              ),
            for (final v in f.ventas)
              ListTile(
                key: Key('compra_${v.folio ?? v.id}'),
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.receipt_long_outlined),
                // Las ventas a crédito del piloto no tienen forma de pago.
                title: Text('${diaEnPalabras(v.fecha)} · '
                    '${v.formaDePago?.etiqueta.toLowerCase() ?? 'crédito (piloto)'}'),
                subtitle: Text(
                  '${v.folio ?? ''} · ${v.vendedor}${v.estado == 'confirmada' ? '' : ' · ${v.estado}'}',
                ),
                trailing: Text(pesos(v.total)),
                onTap: () => _verVenta(v.id),
              ),
            const SizedBox(height: 24),
          ],
        ],
      ),
    );
  }

  void _verVenta(String ventaId) => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => PantallaVentaVista(ventaId: ventaId)),
      );
}
