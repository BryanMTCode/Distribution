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
///   · ¿De qué ruta y qué día? (ADR 0002 §122) No todas en general: abre con las
///     rutas y cuántas tiendas tiene cada día; al elegir una ruta o un día salen
///     sus tiendas agrupadas por ruta y por día, en el orden del recorrido.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sincronizacion.dart';
import '../../estado/vendedores.dart';
import '../canceladas.dart';
import '../editor_de_ubicacion.dart';
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

/// Los días como se trabajan, con el número de la base (`extract(dow)`, 0 domingo).
const _ordenDeDias = [1, 2, 3, 4, 5, 6, 0];
const _diaCorto = {1: 'Lun', 2: 'Mar', 3: 'Mié', 4: 'Jue', 5: 'Vie', 6: 'Sáb', 0: 'Dom'};
const _diaLargo = {
  1: 'Lunes', 2: 'Martes', 3: 'Miércoles', 4: 'Jueves', 5: 'Viernes', 6: 'Sábado',
  0: 'Domingo',
};

String _nombreDeRuta(RutaConTiendas r) =>
    r.codigo == null ? r.nombre : '${r.codigo} · ${r.nombre}';

String _diasEnResumen(List<DiaConTiendas> dias) => [
      for (final d in dias) '${d.dia == null ? 'Sin día' : _diaCorto[d.dia]} ${d.tiendas}',
    ].join(' · ');

class _EstadoClientes extends ConsumerState<PantallaClientesDeOficina> {
  String _filtro = 'todos';
  // La ruta y el día elegidos (ADR 0002 §122): '' ninguno, «sin» las que no tienen.
  String _ruta = '';
  String _dia = '';
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
      final l = await cliente.lista(
        filtro: _filtro,
        q: _busqueda.text,
        ruta: _ruta,
        dia: _dia,
        agrupar: true,
      );
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
    final grupos =
        l != null && l.listado && l.rutas.isNotEmpty ? _grupos(l) : const <_Grupo>[];
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
            if (l != null && l.rutas.isNotEmpty) ..._filtrosDeRutaYDia(l),
            const SizedBox(height: 8),
            if (_error != null) Text(_error!, style: TextStyle(color: colores.error)),
            if (l == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            // Sin ruta ni día: las rutas con sus tiendas de cada día, no todas en
            // general. Tocar una la elige.
            if (l != null && !l.listado)
              for (final r in l.rutas)
                Card(
                  child: ListTile(
                    key: Key('resumen_ruta_${r.codigo ?? 'sin'}'),
                    title: Text(_nombreDeRuta(r)),
                    subtitle: Text(
                      '${r.vendedor == null ? '' : '${r.vendedor} · '}${r.total} '
                      '${r.total == 1 ? 'tienda' : 'tiendas'}\n${_diasEnResumen(r.porDia)}',
                    ),
                    isThreeLine: true,
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => _elegir(ruta: r.clave),
                  ),
                ),
            if (l != null && l.listado && l.clientes.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Text('No hay clientes con ese filtro.', textAlign: TextAlign.center),
              ),
            // Con una ruta o un día: agrupadas por ruta y por día.
            for (final (i, grupo) in grupos.indexed) ...[
              if (i == 0 || grupos[i - 1].rutaClave != grupo.rutaClave)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    grupo.rutaTitulo,
                    key: Key('grupo_ruta_${grupo.rutaCodigo ?? 'sin'}'),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              Padding(
                padding: const EdgeInsets.only(top: 6, bottom: 2),
                child: Text(
                  '${grupo.dia == null ? 'Sin día de visita' : _diaLargo[grupo.dia]} · '
                  '${grupo.clientes.length} ${grupo.clientes.length == 1 ? 'tienda' : 'tiendas'}',
                  key: Key('grupo_dia_${grupo.rutaCodigo ?? 'sin'}_${grupo.dia ?? 'sin'}'),
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ),
              for (final c in grupo.clientes) _tarjeta(c, colores),
            ],
            // Un servidor anterior no agrupa: la lista de siempre.
            if (l != null && l.rutas.isEmpty)
              for (final c in l.clientes) _tarjeta(c, colores),
            if (l != null && l.recortado)
              const Text('Se muestran las primeras. Elige una ruta o un día para ver el resto.'),
          ],
        ),
      ),
    );
  }

  void _elegir({String? ruta, String? dia}) {
    setState(() {
      if (ruta != null) _ruta = ruta;
      if (dia != null) _dia = dia;
    });
    _cargar();
  }

  /// La ruta (todas o una) y el día, con cuántas tiendas tiene cada uno.
  List<Widget> _filtrosDeRutaYDia(ListaDeClientesDeOficina l) {
    final elegida = l.rutas.where((r) => r.clave == _ruta).firstOrNull;
    // Los días de la ruta elegida, o de todas sumadas.
    final porDia = <int?, int>{};
    for (final r in elegida == null ? l.rutas : [elegida]) {
      for (final d in r.porDia) {
        porDia[d.dia] = (porDia[d.dia] ?? 0) + d.tiendas;
      }
    }
    Widget chip(String clave, String etiqueta,
            {required bool elegido, required VoidCallback alTocar}) =>
        Padding(
          padding: const EdgeInsets.only(right: 6),
          child: ChoiceChip(
            key: Key(clave),
            label: Text(etiqueta),
            selected: elegido,
            onSelected: (_) => alTocar(),
          ),
        );
    return [
      const SizedBox(height: 6),
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            chip('ruta_cliente_todas', 'Todas las rutas',
                elegido: _ruta.isEmpty, alTocar: () => _elegir(ruta: '')),
            for (final r in l.rutas)
              chip('ruta_cliente_${r.codigo ?? 'sin'}', '${r.codigo ?? r.nombre} (${r.total})',
                  elegido: _ruta == r.clave, alTocar: () => _elegir(ruta: r.clave)),
          ],
        ),
      ),
      const SizedBox(height: 4),
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            chip('dia_cliente_todos', 'Todos los días',
                elegido: _dia.isEmpty, alTocar: () => _elegir(dia: '')),
            for (final d in _ordenDeDias)
              if (d != 0 || (porDia[0] ?? 0) > 0)
                chip('dia_cliente_$d', '${_diaCorto[d]} ${porDia[d] ?? 0}',
                    elegido: _dia == '$d', alTocar: () => _elegir(dia: '$d')),
            if ((porDia[null] ?? 0) > 0)
              chip('dia_cliente_sin', 'Sin día ${porDia[null]}',
                  elegido: _dia == 'sin', alTocar: () => _elegir(dia: 'sin')),
          ],
        ),
      ),
    ];
  }

  /// Ruta → día → tiendas, en el orden en que llegan (el del recorrido). Una
  /// tienda que toca dos días sale en los dos.
  List<_Grupo> _grupos(ListaDeClientesDeOficina l) {
    final grupos = <String, _Grupo>{};
    final titulos = {for (final r in l.rutas) r.clave: _nombreDeRuta(r)};
    for (final c in l.clientes) {
      final clave = c.rutaId ?? 'sin';
      final dias = c.dias.isEmpty ? <int?>[null] : <int?>[...c.dias];
      for (final d in dias) {
        if (_dia == 'sin' && d != null) continue;
        if (_dia.isNotEmpty && _dia != 'sin' && '$d' != _dia) continue;
        grupos
            .putIfAbsent(
              '$clave|$d',
              () => _Grupo(clave, c.rutaCodigo, titulos[clave] ?? c.ruta ?? 'Sin ruta', d),
            )
            .clientes
            .add(c);
      }
    }
    int ordenDeDia(int? d) => d == null ? 99 : _ordenDeDias.indexOf(d);
    final rutas = [for (final r in l.rutas) r.clave];
    return grupos.values.toList()
      ..sort((a, b) {
        final porRuta = rutas.indexOf(a.rutaClave).compareTo(rutas.indexOf(b.rutaClave));
        return porRuta != 0 ? porRuta : ordenDeDia(a.dia).compareTo(ordenDeDia(b.dia));
      });
  }

  Widget _tarjeta(ClienteDeOficina c, ColorScheme colores) => Card(
        child: ListTile(
          key: Key('cliente_oficina_${c.codigo ?? c.id}'),
          leading: Icon(
            c.conUbicacion ? Icons.storefront_outlined : Icons.location_off_outlined,
            color: c.conUbicacion ? null : colores.error,
          ),
          title: Text(c.nombre),
          subtitle: Text(
            [
              if (c.codigo != null) c.codigo!,
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
      );
}

class _Grupo {
  _Grupo(this.rutaClave, this.rutaCodigo, this.rutaTitulo, this.dia);

  final String rutaClave;
  final String? rutaCodigo;
  final String rutaTitulo;
  final int? dia;
  final List<ClienteDeOficina> clientes = [];
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
            if (f.puedeUbicar) ...[
              const SizedBox(height: 16),
              Text('Fijar o corregir la ubicación', style: estilo.titleMedium),
              const Text('Con el GPS si estás en el negocio; si no, escríbela (por '
                  'ejemplo, copiada de un mapa).'),
              const SizedBox(height: 8),
              EditorDeUbicacion(
                key: const Key('editor_ubicacion_oficina'),
                lat: f.lat,
                lng: f.lng,
                origen: f.ubicacionOrigen,
                alGuardar: (ubicacion) async {
                  final cliente = _cliente(ref);
                  if (cliente == null) {
                    throw const _Texto('No hay sesión en línea. Busca señal.');
                  }
                  try {
                    final nueva = await cliente.ubicar(f.id, ubicacion);
                    if (mounted) setState(() => _f = nueva);
                    return nueva.mensaje ?? 'Ubicación guardada.';
                  } on CargaRechazada catch (e) {
                    throw _Texto(e.detalle);
                  } on Object catch (e) {
                    throw _Texto(explicarErrorDeOficina(e));
                  }
                },
              ),
            ],
            const Divider(height: 32),
            Text('Sus compras', style: estilo.titleMedium),
            if (f.ventas.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('No ha comprado.'),
              ),
            for (final v in f.ventas)
              if (!estaCancelado(v.estado)) _compra(v),
            // Las canceladas, aparte y plegadas (ADR 0002 §95).
            SeccionDeCanceladas(
              renglones: [for (final v in f.ventas) if (estaCancelado(v.estado)) _compra(v)],
            ),
            const SizedBox(height: 24),
          ],
        ],
      ),
    );
  }

  Widget _compra(VentaDelCliente v) => ListTile(
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
      );

  void _verVenta(String ventaId) => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => PantallaVentaVista(ventaId: ventaId)),
      );
}

/// Un error que se lee tal cual en el editor de ubicación.
class _Texto implements Exception {
  const _Texto(this.texto);

  final String texto;

  @override
  String toString() => texto;
}
