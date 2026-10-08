/// Los vendedores, desde el teléfono de la oficina.
///
/// Pedido en operación (octubre 2026): «quiero ver en la app el almacén de cada
/// vendedor, sus detalles, sus ventas y todo eso». Es la pantalla Vendedores del
/// panel, en el bolsillo:
///
///   · La lista: cada vendedor con lo que vendió (y cuánto en efectivo), lo que
///     debe en su cuenta y cuándo habló su teléfono por última vez.
///   · Su ficha: TODO lo que hizo, en orden de hora —ventas, mermas,
///     visitas, cargas, cortes, su cuenta—, y su camión producto por producto.
///   · Cada venta se abre con lo que se le vendió a la tienda.
///
/// Las cifras salen de las mismas consultas que el dashboard: los dos dicen lo
/// mismo. Lo que el vendedor hizo sin señal y no ha subido no está aquí, y la
/// pantalla lo dice con la hora de su último contacto.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sesion.dart';
import '../../estado/vendedores.dart';
import 'comunes.dart';
import 'periodo.dart';

/// El día operativo de hoy, con el reloj de la app (las pruebas lo fijan).
String _hoy(WidgetRef ref) => diaOperativoDe(ref.read(relojProvider)());

String _hora(DateTime? momento) {
  if (momento == null) return '';
  final l = momento.toLocal();
  return '${l.day.toString().padLeft(2, '0')}/${l.month.toString().padLeft(2, '0')} '
      '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
}

/// Cómo se pagó, en palabras. Las ventas a crédito del piloto no traen forma.
String _formaDePago(VentaVista v) {
  final forma = v.formaDePago;
  if (forma == null) return v.tipo == 'credito' ? 'a crédito (piloto)' : 'de contado';
  return switch (v.pagoEstado) {
    'por_confirmar' => '${forma.etiqueta.toLowerCase()} por confirmar',
    'rechazado' => '${forma.etiqueta.toLowerCase()} que no llegó',
    _ => forma.etiqueta.toLowerCase(),
  };
}

class _Error extends StatelessWidget {
  const _Error({required this.texto, this.onReintentar});

  final String texto;
  final VoidCallback? onReintentar;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    return Container(
      key: const Key('aviso_oficina_error'),
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colores.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(texto, style: TextStyle(color: colores.onErrorContainer)),
          if (onReintentar != null)
            TextButton(onPressed: onReintentar, child: const Text('Volver a intentar')),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// La lista
// ---------------------------------------------------------------------------
class PantallaVendedores extends ConsumerStatefulWidget {
  const PantallaVendedores({super.key});

  @override
  ConsumerState<PantallaVendedores> createState() => _EstadoVendedores();
}

class _EstadoVendedores extends ConsumerState<PantallaVendedores> {
  PeriodoElegido _periodo = const PeriodoElegido('hoy');
  ListaDeVendedores? _lista;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteVendedoresProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final lista = await cliente.lista(
        periodo: _periodo.clave,
        desde: _periodo.desde,
        hasta: _periodo.hasta,
      );
      if (!mounted) return;
      setState(() {
        _lista = lista;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final lista = _lista;
    return Scaffold(
      key: const Key('pantalla_vendedores'),
      appBar: AppBar(
        title: const Text('Vendedores'),
        actions: [
          IconButton(
            tooltip: 'Volver a consultar',
            icon: const Icon(Icons.refresh),
            onPressed: _cargar,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (lista != null) ...[
              SelectorDePeriodo(
                periodos: lista.periodos,
                elegido: _periodo,
                hoy: ref.watch(relojProvider)(),
                onElegir: (p) {
                  setState(() => _periodo = p);
                  _cargar();
                },
              ),
              const SizedBox(height: 4),
              Text(
                lista.periodo.enPalabras(hoy: _hoy(ref)),
                key: const Key('dias_de_vendedores'),
                style: Theme.of(context).textTheme.titleSmall,
              ),
              Text(
                'Lo que no han subido todavía no está aquí.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 12),
            ],
            if (_error != null) _Error(texto: _error!, onReintentar: _cargar),
            if (lista == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (lista != null && lista.vendedores.isEmpty)
              const Text('No hay vendedores dados de alta. Se dan de alta en el panel.'),
            for (final v in lista?.vendedores ?? const <VendedorEnLista>[])
              Card(
                child: ListTile(
                  key: Key('vendedor_${v.codigo}'),
                  title: Text(v.activo ? v.nombre : '${v.nombre} (de baja)'),
                  subtitle: Text(
                    '${v.camion ?? 'sin camión'} · ${v.rutas ?? 'sin ruta'}\n'
                    '${v.ventas} venta(s) · ${pesos(v.importe)} · '
                    '${pesos(v.efectivo)} en efectivo'
                    '${v.noVentas > 0 ? ' · ${v.noVentas} sin venta' : ''}\n'
                    '${v.saldoCuenta.centavos > 0 ? 'Debe ${pesos(v.saldoCuenta)} · ' : ''}'
                    'Su teléfono: ${antiguedadEnPalabras(v.ultimoContacto)}',
                  ),
                  isThreeLine: true,
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => PantallaVendedor(
                        vendedorId: v.id,
                        nombre: v.nombre,
                        periodo: _periodo,
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// La ficha: movimientos y camión
// ---------------------------------------------------------------------------
class PantallaVendedor extends ConsumerStatefulWidget {
  const PantallaVendedor({
    super.key,
    required this.vendedorId,
    required this.nombre,
    this.periodo = const PeriodoElegido('hoy'),
  });

  final String vendedorId;
  final String nombre;
  final PeriodoElegido periodo;

  @override
  ConsumerState<PantallaVendedor> createState() => _EstadoVendedor();
}

class _EstadoVendedor extends ConsumerState<PantallaVendedor> {
  late PeriodoElegido _periodo = widget.periodo;
  String _tipo = '';
  DetalleDeVendedor? _detalle;
  CamionDelVendedor? _camion;
  String? _error;
  String? _errorCamion;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _cargarMovimientos();
      _cargarCamion();
    });
  }

  Future<void> _cargarMovimientos() async {
    final cliente = ref.read(clienteVendedoresProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final d = await cliente.detalle(
        widget.vendedorId,
        periodo: _periodo.clave,
        desde: _periodo.desde,
        hasta: _periodo.hasta,
        tipo: _tipo,
      );
      if (!mounted) return;
      setState(() {
        _detalle = d;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  Future<void> _cargarCamion() async {
    final cliente = ref.read(clienteVendedoresProvider);
    if (cliente == null) return;
    try {
      final c = await cliente.camion(widget.vendedorId);
      if (!mounted) return;
      setState(() {
        _camion = c;
        _errorCamion = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _errorCamion = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 2,
        child: Scaffold(
          key: const Key('pantalla_vendedor'),
          appBar: AppBar(
            title: Text(widget.nombre),
            bottom: const TabBar(
              tabs: [
                Tab(key: Key('pestana_movimientos'), text: 'Movimientos'),
                Tab(key: Key('pestana_camion'), text: 'Su camión'),
              ],
            ),
          ),
          body: TabBarView(
            children: [
              RefreshIndicator(onRefresh: _cargarMovimientos, child: _movimientos()),
              RefreshIndicator(onRefresh: _cargarCamion, child: _camionVista()),
            ],
          ),
        ),
      );

  Widget _movimientos() {
    final d = _detalle;
    final estilo = Theme.of(context).textTheme;
    return ListView(
      key: const Key('lista_movimientos'),
      padding: const EdgeInsets.all(16),
      children: [
        if (_error != null) _Error(texto: _error!, onReintentar: _cargarMovimientos),
        if (d == null && _error == null)
          const Padding(
            padding: EdgeInsets.all(32),
            child: Center(child: CircularProgressIndicator()),
          ),
        if (d != null) ...[
          Text(
            '${d.codigo} · ${d.camion ?? 'sin camión'}${d.activo ? '' : ' · de baja'}',
            style: estilo.titleSmall,
          ),
          Text('Rutas: ${d.rutas ?? 'ninguna'}'),
          Text(
            d.saldoCuenta.centavos > 0
                ? 'Debe en su cuenta: ${pesos(d.saldoCuenta)}'
                : 'Su cuenta está al corriente',
            key: const Key('saldo_cuenta_vendedor'),
          ),
          for (final t in d.telefonos)
            Text(
              'Teléfono ${t.etiqueta}${t.estado == 'activo' ? '' : ' (${t.estado})'}: '
              'subió ${antiguedadEnPalabras(t.ultimaSubida)}'
              '${(t.colaPendiente ?? 0) > 0 ? ' · le faltan ${t.colaPendiente} por subir' : ''}',
              style: estilo.bodySmall,
            ),
          const SizedBox(height: 12),
          SelectorDePeriodo(
            periodos: d.periodos,
            elegido: _periodo,
            hoy: ref.watch(relojProvider)(),
            onElegir: (p) {
              setState(() => _periodo = p);
              _cargarMovimientos();
            },
          ),
          const SizedBox(height: 4),
          Text(
            d.periodo.enPalabras(hoy: _hoy(ref)),
            key: const Key('dias_del_vendedor'),
            style: estilo.titleSmall,
          ),
          const SizedBox(height: 8),
          // El resumen es también el filtro: tocar un tipo deja solo ese.
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [
              ChoiceChip(
                key: const Key('tipo_todos'),
                label: Text('Todo (${d.resumen.fold<int>(0, (s, r) => s + r.cuantos)})'),
                selected: _tipo.isEmpty,
                onSelected: (_) {
                  setState(() => _tipo = '');
                  _cargarMovimientos();
                },
              ),
              for (final r in d.resumen)
                ChoiceChip(
                  key: Key('tipo_${r.tipo}'),
                  label: Text(
                    '${r.etiqueta} (${r.cuantos})'
                    '${r.tipo == 'venta' || r.tipo == 'cobro' ? ' ${pesos(r.importe)}' : ''}',
                  ),
                  selected: _tipo == r.tipo,
                  onSelected: (_) {
                    setState(() => _tipo = r.tipo);
                    _cargarMovimientos();
                  },
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (d.movimientos.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text('Sin movimientos en esos días.'),
            ),
          for (final m in d.movimientos) _renglon(m),
          if (d.recortado)
            Text(
              'Se muestran los ${d.limite} más recientes. Elige un tipo o un '
              'periodo más corto para ver el resto.',
              style: estilo.bodySmall,
            ),
        ],
      ],
    );
  }

  Widget _renglon(MovimientoDeVendedor m) {
    final esVenta = m.tipo == 'venta' && m.ref != null;
    final partes = [
      _hora(m.momento).isEmpty ? (m.fecha ?? '') : _hora(m.momento),
      if (m.folio != null) m.folio!,
      if (m.estado != null) m.estado!,
    ];
    return ListTile(
      key: Key('movimiento_${m.tipo}_${m.folio ?? m.ref}'),
      contentPadding: EdgeInsets.zero,
      leading: Icon(_icono(m.tipo)),
      title: Text(
        [if (m.cliente != null) m.cliente!, if (m.detalle != null) m.detalle!].join(' · '),
      ),
      subtitle: Text(partes.join(' · ')),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (m.marca)
            const Padding(
              padding: EdgeInsets.only(right: 4),
              child: Icon(Icons.flag_outlined, color: Colors.orange, size: 18),
            ),
          if (m.importe != null) Text(pesos(m.importe!)),
          if (esVenta) const Icon(Icons.chevron_right),
        ],
      ),
      onTap: esVenta
          ? () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => PantallaVentaVista(ventaId: m.ref!)),
              )
          : null,
    );
  }

  Widget _camionVista() {
    final c = _camion;
    final estilo = Theme.of(context).textTheme;
    return ListView(
      key: const Key('lista_camion'),
      padding: const EdgeInsets.all(16),
      children: [
        if (_errorCamion != null) _Error(texto: _errorCamion!, onReintentar: _cargarCamion),
        if (c == null && _errorCamion == null)
          const Padding(
            padding: EdgeInsets.all(32),
            child: Center(child: CircularProgressIndicator()),
          ),
        if (c != null) ...[
          Text(c.camion ?? 'Sin camión asignado', style: estilo.titleMedium),
          Text(
            '${cantidadLegible(c.piezas)} piezas en total, según el servidor. Lo que '
            'vendió sin señal después de su último contacto '
            '(${antiguedadEnPalabras(c.ultimoContacto)}) todavía no está restado.',
            style: estilo.bodySmall,
          ),
          const SizedBox(height: 8),
          if (c.existencias.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 16),
              child: Text('El camión está vacío.'),
            ),
          for (final e in c.existencias)
            ListTile(
              key: Key('camion_${e.sku}'),
              contentPadding: EdgeInsets.zero,
              title: Text(e.nombre),
              subtitle: e.negativa
                  ? const Text('En negativo: se vendió más de lo que se le cargó. '
                      'Hay que revisarlo.')
                  : null,
              trailing: Text(
                '${cantidadLegible(e.cantidad)} ${e.unidadBase}',
                style: TextStyle(
                  fontWeight: FontWeight.w600,
                  color: e.negativa ? Theme.of(context).colorScheme.error : null,
                ),
              ),
            ),
        ],
      ],
    );
  }
}

IconData _icono(String tipo) => switch (tipo) {
      'venta' => Icons.receipt_long_outlined,
      'cobro' => Icons.payments_outlined,
      'merma' => Icons.delete_sweep_outlined,
      'no_venta' => Icons.storefront_outlined,
      'cliente' => Icons.person_add_alt_outlined,
      'carga' => Icons.local_shipping_outlined,
      'devolucion' => Icons.assignment_return_outlined,
      'corte' => Icons.fact_check_outlined,
      'ajuste' => Icons.tune,
      'cuenta' => Icons.account_balance_wallet_outlined,
      _ => Icons.circle_outlined,
    };

// ---------------------------------------------------------------------------
// Una venta
// ---------------------------------------------------------------------------
class PantallaVentaVista extends ConsumerStatefulWidget {
  const PantallaVentaVista({super.key, required this.ventaId});

  final String ventaId;

  @override
  ConsumerState<PantallaVentaVista> createState() => _EstadoVentaVista();
}

class _EstadoVentaVista extends ConsumerState<PantallaVentaVista> {
  VentaVista? _venta;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteVendedoresProvider);
    if (cliente == null) return;
    try {
      final v = await cliente.venta(widget.ventaId);
      if (!mounted) return;
      setState(() {
        _venta = v;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final v = _venta;
    final estilo = Theme.of(context).textTheme;
    return Scaffold(
      key: const Key('pantalla_venta_vista'),
      appBar: AppBar(title: Text(v?.folio ?? 'Venta')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null) _Error(texto: _error!, onReintentar: _cargar),
          if (v == null && _error == null)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (v != null) ...[
            Text(v.cliente, style: estilo.titleMedium),
            Text(
              '${v.vendedor} · ${_hora(v.momento).isEmpty ? v.fecha : _hora(v.momento)} · '
              '${_formaDePago(v)} · ${v.estado}',
            ),
            const Divider(height: 24),
            for (final p in v.partidas)
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(p.nombre),
                subtitle: Text(
                  '${cantidadLegible(p.cantidad)} ${p.unidad} × ${pesos(p.precioUnitario)}',
                ),
                trailing: Text(pesos(p.importe)),
              ),
            const Divider(height: 24),
            Row(
              children: [
                Expanded(child: Text('Total', style: estilo.titleMedium)),
                Text(pesos(v.total), key: const Key('total_venta_vista'),
                    style: estilo.titleMedium),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
