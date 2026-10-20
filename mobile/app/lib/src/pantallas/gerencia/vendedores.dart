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
///   · Su ruta de cada día (ADR 0002 §121): las tiendas que le tocan, en el orden
///     de su recorrido, las que no tienen ubicación, y hoy, cuáles ya visitó.
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
import '../../marca.dart';
import '../canceladas.dart';
import '../familias.dart';
import 'clientes_oficina.dart';
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
  const PantallaVendedores({
    super.key,
    this.desactivados = false,
    this.periodoInicial = const PeriodoElegido('hoy'),
  });

  /// Con qué periodo abre. Desde Empresa, «vendido este mes» abre el mes.
  final PeriodoElegido periodoInicial;

  /// La lista aparte de los desactivados (ADR 0002 §93): no se mezclan con los
  /// que trabajan.
  final bool desactivados;

  @override
  ConsumerState<PantallaVendedores> createState() => _EstadoVendedores();
}

class _EstadoVendedores extends ConsumerState<PantallaVendedores> {
  late PeriodoElegido _periodo = widget.periodoInicial;
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
        desactivados: widget.desactivados,
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
      key: Key(widget.desactivados ? 'pantalla_vendedores_desactivados' : 'pantalla_vendedores'),
      appBar: AppBar(
        title: Text(widget.desactivados ? 'Vendedores desactivados' : 'Vendedores'),
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
              Text(
                widget.desactivados
                    ? 'Ningún vendedor desactivado.'
                    : 'No hay vendedores activos. Se dan de alta en el panel.',
              ),
            for (final v in lista?.vendedores ?? const <VendedorEnLista>[])
              Card(
                child: ListTile(
                  key: Key('vendedor_${v.codigo}'),
                  title: Text(v.nombre),
                  subtitle: Text(
                    '${v.camion ?? 'sin camión'} · ${v.rutas ?? 'sin ruta'}\n'
                    '${v.ventas} venta(s) · ${pesos(v.importe)}'
                    // Toda venta es en efectivo (§81): el efectivo solo se dice
                    // si es otra cifra —una transferencia de un teléfono viejo—.
                    '${v.efectivo.centavos == v.importe.centavos ? '' : ' · ${pesos(v.efectivo)} en efectivo'}'
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
            // Los desactivados, aparte (ADR 0002 §93).
            if (!widget.desactivados && (lista?.desactivados ?? 0) > 0)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: OutlinedButton.icon(
                  key: const Key('ver_vendedores_desactivados'),
                  icon: const Icon(Icons.person_off_outlined),
                  label: Text('Desactivados (${lista!.desactivados})'),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const PantallaVendedores(desactivados: true),
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

class _EstadoVendedor extends ConsumerState<PantallaVendedor>
    with FamiliasPlegables<PantallaVendedor> {
  late PeriodoElegido _periodo = widget.periodo;
  String _tipo = '';
  DetalleDeVendedor? _detalle;
  CamionDelVendedor? _camion;
  String? _error;
  String? _errorCamion;
  // Su ruta: el día elegido (nulo = el de hoy, que decide el servidor).
  int? _diaRuta;
  RutaDelVendedor? _ruta;
  String? _errorRuta;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _cargarMovimientos();
      _cargarCamion();
      _cargarRuta();
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

  Future<void> _cargarRuta() async {
    final cliente = ref.read(clienteVendedoresProvider);
    if (cliente == null) return;
    try {
      final r = await cliente.ruta(widget.vendedorId, dia: _diaRuta, fecha: _hoy(ref));
      if (!mounted) return;
      setState(() {
        _ruta = r;
        _errorRuta = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _errorRuta = explicarErrorDeOficina(e));
    }
  }

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 3,
        child: Scaffold(
          key: const Key('pantalla_vendedor'),
          appBar: AppBar(
            title: Text(widget.nombre),
            bottom: const TabBar(
              tabs: [
                Tab(key: Key('pestana_movimientos'), text: 'Movimientos'),
                Tab(key: Key('pestana_camion'), text: 'Su camión'),
                Tab(key: Key('pestana_ruta'), text: 'Su ruta'),
              ],
            ),
          ),
          body: TabBarView(
            children: [
              RefreshIndicator(onRefresh: _cargarMovimientos, child: _movimientos()),
              RefreshIndicator(onRefresh: _cargarCamion, child: _camionVista()),
              RefreshIndicator(onRefresh: _cargarRuta, child: _rutaVista()),
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
          for (final m in d.movimientos)
            if (!estaCancelado(m.estado)) _renglon(m),
          // Lo cancelado, aparte y plegado (ADR 0002 §95).
          SeccionDeCanceladas(
            renglones: [
              for (final m in d.movimientos)
                if (estaCancelado(m.estado)) _renglon(m),
            ],
          ),
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
          // Por familia, como el camión del vendedor y las existencias del
          // almacén (ADR 0002 §114): cada una se pliega con un toque y dice
          // cuántas piezas trae.
          for (final entrada in entradasPorFamilia(
            c.existencias,
            _familiaDe,
            plegadas: plegadas,
          ))
            switch (entrada) {
              EncabezadoEntrada(:final grupo, :final abierta) => Padding(
                  padding: const EdgeInsets.only(top: 12, bottom: 2),
                  child: EncabezadoDeFamilia(
                    familia: grupo.familia,
                    cuantos: grupo.elementos.length,
                    abierta: abierta,
                    resumen: '${_piezasDe(grupo.elementos)} pzas',
                    alTocar: () => alternarFamilia(grupo.familia),
                  ),
                ),
              ElementoEntrada(elemento: final e) => ListTile(
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
            },
        ],
      ],
    );
  }

  Widget _rutaVista() {
    final r = _ruta;
    final estilo = Theme.of(context).textTheme;
    final colores = Theme.of(context).colorScheme;
    return ListView(
      key: const Key('lista_ruta'),
      padding: const EdgeInsets.all(16),
      children: [
        if (_errorRuta != null) _Error(texto: _errorRuta!, onReintentar: _cargarRuta),
        if (r == null && _errorRuta == null)
          const Padding(
            padding: EdgeInsets.all(32),
            child: Center(child: CircularProgressIndicator()),
          ),
        if (r != null) ...[
          Text(
            r.rutas.isEmpty ? 'Sin ruta asignada' : 'Ruta ${r.rutas.join(', ')}',
            style: estilo.titleMedium,
          ),
          const SizedBox(height: 8),
          // Los días con cuántas tiendas le tocan: tocar uno muestra las suyas.
          Wrap(
            spacing: 6,
            runSpacing: 4,
            children: [
              for (final d in r.dias)
                ChoiceChip(
                  key: Key('dia_ruta_${d.dia}'),
                  label: Text('${d.etiqueta} ${d.tiendas}'),
                  selected: d.dia == r.dia,
                  onSelected: (_) {
                    setState(() => _diaRuta = d.dia);
                    _cargarRuta();
                  },
                ),
            ],
          ),
          const SizedBox(height: 8),
          Text(_resumenDeRuta(r), key: const Key('resumen_ruta'), style: estilo.titleSmall),
          if (r.sinUbicacion > 0)
            Text(
              '${r.sinUbicacion} sin ubicación: el vendedor la captura al llegar a la tienda.',
              key: const Key('sin_ubicacion_ruta'),
              style: estilo.bodySmall,
            ),
          const SizedBox(height: 4),
          if (r.tiendas.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text('No le toca ninguna tienda el ${_diaEnPalabras(r.dia)}.'),
            ),
          for (final t in r.tiendas)
            ListTile(
              key: Key('tienda_ruta_${t.codigo ?? t.id}'),
              contentPadding: EdgeInsets.zero,
              leading: _ordenDeLaTienda(t, esHoy: r.esHoy, estilo: estilo),
              title: Text(t.nombre),
              subtitle: Text(
                [
                  if (t.codigo != null) t.codigo!,
                  if (t.direccion != null) t.direccion!,
                  if (r.rutas.length > 1) t.ruta,
                  if (t.semanas.isNotEmpty) 'solo la semana ${t.semanas.join(' y ')}',
                  if (t.reprogramada) 'reprogramada para hoy',
                  if (!t.conUbicacion) 'sin ubicación',
                ].join(' · '),
              ),
              trailing: _visita(t, r.esHoy, colores),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => PantallaFichaDeCliente(clienteId: t.id, nombre: t.nombre),
                ),
              ),
            ),
        ],
      ],
    );
  }

  /// El número de orden. Hoy, la que ya compró va en verde y la visitada sin
  /// venta en gris; las que faltan, en el rojo de la marca (ADR 0002 §153).
  Widget _ordenDeLaTienda(TiendaDeRuta t, {required bool esHoy, required TextTheme estilo}) {
    final (fondo, letra) = switch (esHoy ? t.visita : null) {
      'venta' => (colorDeVendida, Colors.white),
      'no_venta' => (colorDeSinVenta, letraDeSinVenta),
      _ => (null, null),
    };
    return CircleAvatar(
      key: Key('orden_ruta_${t.codigo ?? t.id}'),
      radius: 15,
      backgroundColor: fondo,
      child: Text(
        t.orden == null ? '·' : '${t.orden}',
        style: estilo.labelMedium?.copyWith(color: letra),
      ),
    );
  }

  /// Lo que ya pasó hoy en la tienda; los demás días, nada.
  Widget? _visita(TiendaDeRuta t, bool esHoy, ColorScheme colores) {
    if (!esHoy) return const Icon(Icons.chevron_right);
    return switch (t.visita) {
      'venta' => Text(
          t.importe == null ? 'vendió' : pesos(t.importe!),
          style: const TextStyle(color: Colors.green, fontWeight: FontWeight.w600),
        ),
      'no_venta' => Text('sin venta', style: TextStyle(color: colores.error)),
      _ => Text('pendiente', style: TextStyle(color: colores.outline)),
    };
  }
}

const _nombresDeDia = {
  0: 'domingo', 1: 'lunes', 2: 'martes', 3: 'miércoles', 4: 'jueves', 5: 'viernes',
  6: 'sábado',
};

String _diaEnPalabras(int dia) => _nombresDeDia[dia] ?? 'día $dia';

/// «El lunes: 45 tiendas», y si es hoy, cuántas lleva visitadas.
String _resumenDeRuta(RutaDelVendedor r) {
  final cuantas = r.tiendas.length;
  final base = '${r.esHoy ? 'Hoy, ${_diaEnPalabras(r.dia)}' : 'El ${_diaEnPalabras(r.dia)}'}: '
      '$cuantas ${cuantas == 1 ? 'tienda' : 'tiendas'}';
  if (!r.esHoy || cuantas == 0) return base;
  final visitadas = r.tiendas.where((t) => t.visita != null).length;
  final conVenta = r.tiendas.where((t) => t.visita == 'venta').length;
  return '$base · visitadas $visitadas ($conVenta con venta), faltan ${cuantas - visitadas}';
}

String? _familiaDe(ExistenciaEnCamion e) => e.familia;

/// Las piezas de una familia, sumadas en milésimos: sin pasar por `double`.
String _piezasDe(List<ExistenciaEnCamion> existencias) => cantidadLegible(
      existencias
          .fold(Cantidad.cero, (suma, e) => suma + Cantidad.deTexto(e.cantidad))
          .texto,
    );

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
