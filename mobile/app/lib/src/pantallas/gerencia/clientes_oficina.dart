/// Los clientes, desde el teléfono de la oficina.
///
/// Pedido en operación (octubre 2026): «agrega lo de los clientes en la app del
/// gerente». Lo que la oficina pregunta de una tienda:
///
///   · ¿Quién me debe, y quién ya se pasó del plazo? La lista abre con lo que
///     más urge cobrar arriba, y filtra por con saldo, vencidos, bloqueados.
///   · ¿Qué me debe esta tienda, nota por nota, y desde cuándo?
///   · ¿Qué compra? Sus ventas —cada una se abre con lo que se le vendió— y sus
///     abonos.
///   · Bloquear o desbloquear su crédito, con motivo (quien tenga el permiso).
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
  ('con_saldo', 'Con saldo'),
  ('vencidos', 'Vencidos'),
  ('bloqueados', 'Bloqueados'),
  ('prospectos', 'Prospectos'),
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
                    c.bloqueado ? Icons.block : Icons.storefront_outlined,
                    color: c.bloqueado || c.saldoVencido.centavos > 0 ? colores.error : null,
                  ),
                  title: Text(c.nombre),
                  subtitle: Text(
                    [
                      c.ruta ?? 'sin ruta',
                      if (c.estatus == 'prospecto') 'prospecto',
                      if (c.bloqueado) 'crédito bloqueado',
                      c.ultimaCompra == null
                          ? 'nunca ha comprado'
                          : 'última compra: ${diaEnPalabras(c.ultimaCompra!)}',
                    ].join(' · '),
                  ),
                  trailing: c.saldo.centavos == 0
                      ? const Icon(Icons.chevron_right)
                      : Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            Text('Debe ${pesos(c.saldo)}'),
                            if (c.saldoVencido.centavos > 0)
                              Text(
                                'vencido ${pesos(c.saldoVencido)}',
                                style: TextStyle(color: colores.error, fontSize: 12),
                              ),
                          ],
                        ),
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
  bool _ocupado = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _hacer((c) => c.ficha(widget.clienteId)));
  }

  Future<void> _hacer(Future<FichaDelCliente> Function(ClienteClientesDeOficina) accion) async {
    final cliente = _cliente(ref);
    if (cliente == null) return;
    setState(() => _ocupado = true);
    try {
      final f = await accion(cliente);
      if (!mounted) return;
      setState(() {
        _f = f;
        _error = null;
        _ocupado = false;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _error = explicarErrorDeOficina(e);
        _ocupado = false;
      });
    }
  }

  Future<void> _cambiarBloqueo(FichaDelCliente f) async {
    if (f.bloqueado) {
      await _hacer((c) => c.bloquear(f.id, bloquear: false));
      return;
    }
    final motivo = await showDialog<String>(
      context: context,
      builder: (_) => const _DialogoBloqueo(),
    );
    if (motivo == null) return;
    await _hacer((c) => c.bloquear(f.id, bloquear: true, motivo: motivo));
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
              key: const Key('tarjeta_credito_cliente'),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Debe ${pesos(f.saldo)}', style: estilo.headlineSmall),
                    if (f.saldoVencido.centavos > 0)
                      Text(
                        'Vencido: ${pesos(f.saldoVencido)}',
                        style: TextStyle(color: colores.error, fontWeight: FontWeight.w600),
                      ),
                    if (f.porConfirmar.centavos > 0)
                      Text('${pesos(f.porConfirmar)} en transferencias por confirmar'),
                    const SizedBox(height: 4),
                    Text(
                      !f.permiteCredito
                          ? 'Sin crédito: solo de contado.'
                          : 'Crédito: ${pesos(f.limiteCredito)}'
                              '${f.diasCredito == null ? '' : ' a ${f.diasCredito} días'}'
                              ' · disponible ${pesos(f.disponible)}',
                    ),
                    if (f.bloqueado)
                      Text(
                        'Crédito BLOQUEADO${f.bloqueoMotivo == null ? '' : ': ${f.bloqueoMotivo}'}',
                        style: TextStyle(color: colores.error, fontWeight: FontWeight.w600),
                      ),
                    Text(
                      'Compró ${pesos(f.compradoMes)} este mes · ${pesos(f.compradoAnio)} este año',
                      style: estilo.bodySmall,
                    ),
                    if (f.puedeBloquear) ...[
                      const SizedBox(height: 8),
                      OutlinedButton.icon(
                        key: const Key('boton_bloqueo_cliente'),
                        onPressed: _ocupado ? null : () => _cambiarBloqueo(f),
                        icon: Icon(f.bloqueado ? Icons.lock_open : Icons.block),
                        label: Text(f.bloqueado ? 'Desbloquear el crédito' : 'Bloquear el crédito'),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text('Lo que debe, nota por nota', style: estilo.titleMedium),
            if (f.cuentas.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('No debe nada.'),
              ),
            for (final c in f.cuentas)
              ListTile(
                key: Key('cuenta_${c.folio ?? c.ventaId}'),
                contentPadding: EdgeInsets.zero,
                title: Text('${c.folio ?? 'Nota'} · ${diaEnPalabras(c.emision)}'),
                subtitle: Text(
                  c.vencida
                      ? 'Vencida hace ${c.diasVencida} día(s) · pagó ${pesos(c.pagado)} de ${pesos(c.original)}'
                      : 'Vence el ${diaEnPalabras(c.vencimiento)} · pagó ${pesos(c.pagado)} de ${pesos(c.original)}',
                  style: TextStyle(color: c.vencida ? colores.error : null),
                ),
                trailing: Text(pesos(c.saldo)),
                onTap: () => _verVenta(c.ventaId),
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
                title: Text('${diaEnPalabras(v.fecha)} · ${v.tipo == 'credito' ? 'crédito' : 'contado'}'),
                subtitle: Text(
                  '${v.folio ?? ''} · ${v.vendedor}${v.estado == 'confirmada' ? '' : ' · ${v.estado}'}',
                ),
                trailing: Text(pesos(v.total)),
                onTap: () => _verVenta(v.id),
              ),
            const Divider(height: 32),
            Text('Sus abonos', style: estilo.titleMedium),
            if (f.cobros.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('Sin abonos.'),
              ),
            for (final k in f.cobros)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.payments_outlined),
                title: Text('${diaEnPalabras(k.fecha)} · ${k.formaPago}'),
                subtitle: Text(
                  '${k.folio ?? ''} · ${k.vendedor} · ${k.estado.replaceAll('_', ' ')}',
                ),
                trailing: Text(pesos(k.importe)),
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

/// El motivo del bloqueo. El vendedor va a preguntar por qué no le puede fiar.
class _DialogoBloqueo extends StatefulWidget {
  const _DialogoBloqueo();

  @override
  State<_DialogoBloqueo> createState() => _EstadoDialogoBloqueo();
}

class _EstadoDialogoBloqueo extends State<_DialogoBloqueo> {
  final _motivo = TextEditingController();

  @override
  void dispose() {
    _motivo.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Bloquear el crédito'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Podrá seguir comprando de contado. Al vendedor le llega el motivo.'),
            const SizedBox(height: 8),
            TextField(
              key: const Key('campo_motivo_bloqueo'),
              controller: _motivo,
              maxLength: 300,
              decoration: const InputDecoration(
                labelText: '¿Por qué?',
                border: OutlineInputBorder(),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancelar')),
          FilledButton(
            key: const Key('boton_bloquear_de_verdad'),
            onPressed: () => Navigator.of(context).pop(_motivo.text.trim()),
            child: const Text('Bloquear'),
          ),
        ],
      );
}
