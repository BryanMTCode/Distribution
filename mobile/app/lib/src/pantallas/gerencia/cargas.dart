/// Cargar el camión desde el teléfono de la oficina.
///
/// ─────────────────────────────────────────────────────────────────────────
/// PARA QUIÉN
/// ─────────────────────────────────────────────────────────────────────────
/// Admin, supervisor y gerente. Pedido en operación (octubre 2026): a las seis
/// de la mañana quien carga está en la bodega con el teléfono en la mano, no
/// frente a la computadora del panel. El vendedor no ve esta pantalla y, si
/// armara la petición a mano, el servidor le contesta 403.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL MISMO CAMINO QUE EL PANEL
/// ─────────────────────────────────────────────────────────────────────────
/// Abrir → capturar bultos de lo que hay en la bodega → confirmar. Las reglas
/// las pone el servidor y son las mismas del panel: bultos enteros, la caja se
/// convierte a piezas al capturar, y no se confirma encima de un día que no ha
/// subido salvo diciendo por qué. Esta pantalla solo muestra lo que el servidor
/// contesta, incluido su mensaje.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/cargas.dart';

/// La lista de cargas abiertas, y el botón para abrir una nueva.
class PantallaCargas extends ConsumerStatefulWidget {
  const PantallaCargas({super.key, this.conBarra = true});

  /// Sin barra cuando va dentro de la pestaña Almacén, que ya pone la suya.
  final bool conBarra;

  @override
  ConsumerState<PantallaCargas> createState() => _EstadoCargas();
}

class _EstadoCargas extends ConsumerState<PantallaCargas> {
  List<ResumenDeCarga>? _cargas;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _cargar());
  }

  Future<void> _cargar() async {
    final cliente = ref.read(clienteCargasProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return;
    }
    try {
      final cargas = await cliente.abiertas();
      if (!mounted) return;
      setState(() {
        _cargas = cargas;
        _error = null;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _error = explicarErrorDeCarga(e));
    }
  }

  Future<void> _abrirDetalle(String id) async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => PantallaDetalleDeCarga(cargaId: id)),
    );
    await _cargar();
  }

  Future<void> _nueva() async {
    final cliente = ref.read(clienteCargasProvider);
    if (cliente == null) return;
    final OpcionesDeCarga opciones;
    try {
      opciones = await cliente.opciones();
    } on Object catch (e) {
      if (!mounted) return;
      _avisar(explicarErrorDeCarga(e));
      return;
    }
    if (!mounted) return;
    if (opciones.vendedores.isEmpty || opciones.bodegas.isEmpty) {
      _avisar(opciones.vendedores.isEmpty
          ? 'No hay vendedores con camión asignado. Se asigna en el panel.'
          : 'No hay bodegas dadas de alta.');
      return;
    }
    final elegido = await showModalBottomSheet<(String, String)>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _NuevaCarga(opciones: opciones),
    );
    if (elegido == null || !mounted) return;
    try {
      final carga = await cliente.abrir(vendedorId: elegido.$1, bodegaId: elegido.$2);
      if (!mounted) return;
      await _abrirDetalle(carga.id);
    } on Object catch (e) {
      if (!mounted) return;
      _avisar(explicarErrorDeCarga(e));
    }
  }

  void _avisar(String texto) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(texto), duration: const Duration(seconds: 6)));

  @override
  Widget build(BuildContext context) {
    final cargas = _cargas;
    return Scaffold(
      key: const Key('pantalla_cargas'),
      appBar: !widget.conBarra
          ? null
          : AppBar(
        title: const Text('Cargas del camión'),
        actions: [
          IconButton(
            tooltip: 'Volver a consultar',
            icon: const Icon(Icons.refresh),
            onPressed: _cargar,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('boton_nueva_carga'),
        onPressed: _nueva,
        icon: const Icon(Icons.add),
        label: const Text('Nueva carga'),
      ),
      body: RefreshIndicator(
        onRefresh: _cargar,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            if (_error != null) _Aviso(texto: _error!, esError: true),
            if (cargas == null && _error == null)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            if (cargas != null && cargas.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'No hay cargas abiertas. Toca «Nueva carga» para subirle '
                  'mercancía a un camión.',
                  key: Key('cargas_vacio'),
                  textAlign: TextAlign.center,
                ),
              ),
            for (final c in cargas ?? const <ResumenDeCarga>[])
              Card(
                child: ListTile(
                  key: Key('carga_${c.folio}'),
                  leading: Icon(
                    c.estado == 'borrador'
                        ? Icons.edit_note
                        : Icons.local_shipping_outlined,
                  ),
                  title: Text('${c.vendedor} · ${c.camion}'),
                  subtitle: Text(
                    '${c.folio} · ${diaEnPalabras(c.fecha)} · ${_estado(c.estado)}\n'
                    '${c.renglones} producto(s), ${cantidadLegible(c.piezas)} piezas',
                  ),
                  isThreeLine: true,
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _abrirDetalle(c.id),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

String _estado(String estado) => switch (estado) {
      'borrador' => 'capturando',
      'confirmada' => 'en el camión',
      'en_ruta' => 'en el corte',
      'cancelada' => 'cancelada',
      _ => estado,
    };

/// A quién y de qué bodega. El camión no se elige: es el del vendedor.
class _NuevaCarga extends StatefulWidget {
  const _NuevaCarga({required this.opciones});

  final OpcionesDeCarga opciones;

  @override
  State<_NuevaCarga> createState() => _EstadoNuevaCarga();
}

class _EstadoNuevaCarga extends State<_NuevaCarga> {
  String? _vendedor;
  late String _bodega = widget.opciones.bodegas.first.id;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        16, 16, 16, 16 + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Nueva carga', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 16),
          DropdownButtonFormField<String>(
            key: const Key('campo_vendedor_carga'),
            initialValue: _vendedor,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Vendedor',
              border: OutlineInputBorder(),
            ),
            items: [
              for (final v in widget.opciones.vendedores)
                DropdownMenuItem(
                  value: v.id,
                  child: Text('${v.nombre} · ${v.camion ?? ''}'),
                ),
            ],
            onChanged: (v) => setState(() => _vendedor = v),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            key: const Key('campo_bodega_carga'),
            initialValue: _bodega,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Sale de la bodega',
              border: OutlineInputBorder(),
            ),
            items: [
              for (final b in widget.opciones.bodegas)
                DropdownMenuItem(value: b.id, child: Text(b.nombre)),
            ],
            onChanged: (b) => setState(() => _bodega = b ?? _bodega),
          ),
          const SizedBox(height: 16),
          FilledButton(
            key: const Key('boton_abrir_carga'),
            onPressed: _vendedor == null
                ? null
                : () => Navigator.of(context).pop((_vendedor!, _bodega)),
            child: const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text('Abrir la carga'),
            ),
          ),
        ],
      ),
    );
  }
}

/// Una carga: lo que lleva, lo que hay en la bodega para subir, y confirmar.
class PantallaDetalleDeCarga extends ConsumerStatefulWidget {
  const PantallaDetalleDeCarga({super.key, required this.cargaId});

  final String cargaId;

  @override
  ConsumerState<PantallaDetalleDeCarga> createState() => _EstadoDetalle();
}

class _EstadoDetalle extends ConsumerState<PantallaDetalleDeCarga> {
  DetalleDeCarga? _carga;
  String? _error;
  bool _ocupado = false;

  /// Lo tecleado, por producto. Se limpia en cuanto el servidor lo acepta.
  final Map<String, TextEditingController> _cantidades = {};
  final Map<String, String> _unidades = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _hacer((c) => c.ver(widget.cargaId)));
  }

  @override
  void dispose() {
    for (final c in _cantidades.values) {
      c.dispose();
    }
    super.dispose();
  }

  TextEditingController _campo(String productoId) =>
      _cantidades.putIfAbsent(productoId, TextEditingController.new);

  /// Toda acción pasa por aquí: el servidor contesta con la carga completa y
  /// su mensaje, y eso es lo que se pinta.
  Future<bool> _hacer(Future<DetalleDeCarga> Function(ClienteCargas) accion) async {
    final cliente = ref.read(clienteCargasProvider);
    if (cliente == null) {
      setState(() => _error = 'No hay sesión en línea. Sal y vuelve a entrar con señal.');
      return false;
    }
    setState(() => _ocupado = true);
    try {
      final carga = await accion(cliente);
      if (!mounted) return true;
      setState(() {
        _carga = carga;
        _error = null;
        _ocupado = false;
      });
      return true;
    } on Object catch (e) {
      if (!mounted) return false;
      setState(() {
        _error = explicarErrorDeCarga(e);
        _ocupado = false;
      });
      return false;
    }
  }

  List<PedidoDeCarga> _pedidos(DetalleDeCarga carga) => [
        for (final p in carga.surtido)
          if ((_cantidades[p.productoId]?.text.trim() ?? '').isNotEmpty)
            PedidoDeCarga(
              productoId: p.productoId,
              unidad: _unidades[p.productoId] ??
                  p.porOmision ??
                  (p.presentaciones.isEmpty ? p.unidadBase : p.presentaciones.first.unidad),
              cantidad: _cantidades[p.productoId]!.text.trim(),
            ),
      ];

  Future<void> _agregar(DetalleDeCarga carga) async {
    final pedidos = _pedidos(carga);
    if (pedidos.isEmpty) {
      setState(() => _error = 'No escribiste ninguna cantidad. Llena los productos '
          'que suben al camión.');
      return;
    }
    final ok = await _hacer((c) => c.agregar(carga.id, pedidos));
    if (ok) {
      for (final c in _cantidades.values) {
        c.clear();
      }
    }
  }

  Future<void> _confirmar(DetalleDeCarga carga) async {
    // `null` es «todavía no»; texto vacío es confirmar sin motivo.
    final motivo = await showDialog<String>(
      context: context,
      builder: (_) => _DialogoConfirmar(carga: carga),
    );
    if (motivo == null) return;
    await _hacer(
      (c) => c.confirmar(carga.id, motivoForzado: motivo.isEmpty ? null : motivo),
    );
  }

  Future<void> _cancelar(DetalleDeCarga carga) async {
    final seguro = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        title: const Text('¿Cancelar esta carga?'),
        content: const Text('No se movió nada todavía: solo se descarta lo capturado.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(contexto).pop(false),
            child: const Text('No'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(contexto).pop(true),
            child: const Text('Sí, cancelar'),
          ),
        ],
      ),
    );
    if (seguro == true) await _hacer((c) => c.cancelar(carga.id));
  }

  @override
  Widget build(BuildContext context) {
    final carga = _carga;
    return Scaffold(
      key: const Key('pantalla_detalle_carga'),
      appBar: AppBar(
        title: Text(carga?.folio ?? 'Carga'),
        actions: [
          if (carga != null && carga.editable)
            IconButton(
              key: const Key('boton_cancelar_carga'),
              tooltip: 'Cancelar la carga',
              icon: const Icon(Icons.delete_outline),
              onPressed: _ocupado ? null : () => _cancelar(carga),
            ),
        ],
      ),
      bottomNavigationBar: carga == null || !carga.editable
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        key: const Key('boton_agregar_a_la_carga'),
                        onPressed: _ocupado ? null : () => _agregar(carga),
                        child: const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('Agregar'),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton(
                        key: const Key('boton_confirmar_carga'),
                        onPressed: _ocupado || carga.renglones.isEmpty
                            ? null
                            : () => _confirmar(carga),
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
      body: carga == null
          ? Center(
              child: _error == null
                  ? const CircularProgressIndicator()
                  : Padding(
                      padding: const EdgeInsets.all(16),
                      child: _Aviso(texto: _error!, esError: true),
                    ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  '${carga.vendedor} · ${carga.camion}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                Text(
                  'Sale de ${carga.bodega} · ${diaEnPalabras(carga.fecha)} · '
                  '${_estado(carga.estado)}',
                ),
                const SizedBox(height: 12),
                if (_error != null) _Aviso(texto: _error!, esError: true),
                if (carga.mensaje != null && _error == null)
                  _Aviso(
                    texto: carga.mensaje!,
                    esError: carga.mensaje!.contains('NO entraron') ||
                        carga.mensaje!.startsWith('No se agregó'),
                  ),
                for (final b in carga.bloqueos) _Aviso(texto: b, esError: true),
                // Por qué no se puede capturar. Sin esto, abrir la carga de un
                // vendedor que YA la tenía confirmada se veía como «no me deja
                // cargar», sin un solo botón ni una razón.
                if (!carga.editable && carga.estado != 'cancelada')
                  const _Aviso(
                    key: Key('aviso_carga_ya_confirmada'),
                    texto: 'Esta carga ya se confirmó: la mercancía salió de la bodega '
                        'y ya no se edita. Cada vendedor lleva UNA carga por día. Si '
                        'hoy necesita más mercancía, súbesela con un ajuste de su '
                        'camión en el panel (Inventario → su camión → Ajustar), o '
                        'cárgasela mañana.',
                    esError: true,
                  ),
                const SizedBox(height: 8),
                Text('En la carga', style: Theme.of(context).textTheme.titleSmall),
                if (carga.renglones.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text('Todavía nada. Escribe cuántos suben abajo y toca «Agregar».'),
                  ),
                for (final r in carga.renglones)
                  ListTile(
                    key: Key('renglon_carga_${r.sku}'),
                    contentPadding: EdgeInsets.zero,
                    title: Text(r.nombre),
                    subtitle: Text('${cantidadLegible(r.cantidad)} ${r.unidadBase}'),
                    trailing: carga.editable
                        ? IconButton(
                            tooltip: 'Quitar',
                            icon: const Icon(Icons.remove_circle_outline),
                            onPressed: _ocupado
                                ? null
                                : () => _hacer((c) => c.quitar(carga.id, r.id)),
                          )
                        : null,
                  ),
                if (carga.editable) ...[
                  const Divider(height: 32),
                  Text('En la bodega', style: Theme.of(context).textTheme.titleSmall),
                  if (carga.surtido.isEmpty)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        'La bodega no tiene existencias registradas, así que no hay '
                        'qué subir. Primero da entrada a la mercancía: Almacén → '
                        'Entradas.',
                        key: Key('aviso_bodega_vacia'),
                      ),
                    ),
                  for (final p in carga.surtido) _renglonDeBodega(p),
                  const SizedBox(height: 24),
                ],
              ],
            ),
    );
  }

  Widget _renglonDeBodega(ProductoEnBodega p) {
    final unidades = [for (final u in p.presentaciones) u.unidad];
    final unidad = _unidades[p.productoId] ??
        p.porOmision ??
        (unidades.isEmpty ? p.unidadBase : unidades.first);
    final ya = cantidadLegible(p.yaEnLaCarga);
    return Padding(
      key: Key('bodega_${p.sku}'),
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p.nombre),
                Text(
                  'Hay ${cantidadLegible(p.enBodega)} ${p.unidadBase}'
                  '${ya == '0' ? '' : ' · ya llevas $ya'}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            width: 64,
            child: TextField(
              key: Key('cantidad_carga_${p.sku}'),
              controller: _campo(p.productoId),
              keyboardType: TextInputType.number,
              textAlign: TextAlign.center,
              decoration: const InputDecoration(
                isDense: true,
                border: OutlineInputBorder(),
              ),
            ),
          ),
          const SizedBox(width: 8),
          DropdownButton<String>(
            key: Key('unidad_carga_${p.sku}'),
            value: unidades.contains(unidad) ? unidad : null,
            items: [
              for (final u in unidades) DropdownMenuItem(value: u, child: Text(u)),
            ],
            onChanged: (u) => setState(() => _unidades[p.productoId] = u ?? unidad),
          ),
        ],
      ),
    );
  }
}

/// Dueño de su campo de texto: el controlador vive lo que vive el diálogo,
/// incluida la animación de salida.
class _DialogoConfirmar extends StatefulWidget {
  const _DialogoConfirmar({required this.carga});

  final DetalleDeCarga carga;

  @override
  State<_DialogoConfirmar> createState() => _EstadoDialogoConfirmar();
}

class _EstadoDialogoConfirmar extends State<_DialogoConfirmar> {
  final _motivo = TextEditingController();

  @override
  void dispose() {
    _motivo.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final carga = widget.carga;
    return AlertDialog(
      title: const Text('¿Confirmar la carga?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'La mercancía sale de ${carga.bodega} y entra al camión de '
              '${carga.vendedor}. Su teléfono la recibe al sincronizar. Ya no '
              'se puede editar: lo que regrese se cuenta en el corte.',
            ),
            if (carga.bloqueos.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                'Hay pendientes: ${carga.bloqueos.join(' ')}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
              const SizedBox(height: 8),
              TextField(
                key: const Key('campo_motivo_forzado'),
                controller: _motivo,
                maxLength: 300,
                decoration: const InputDecoration(
                  labelText: '¿Por qué tiene que salir así?',
                  helperText: 'Queda registrado.',
                  border: OutlineInputBorder(),
                ),
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Todavía no'),
        ),
        FilledButton(
          key: const Key('boton_confirmar_de_verdad'),
          onPressed: () => Navigator.of(context).pop(_motivo.text.trim()),
          child: const Text('Confirmar'),
        ),
      ],
    );
  }
}

class _Aviso extends StatelessWidget {
  const _Aviso({super.key, required this.texto, required this.esError});

  final String texto;
  final bool esError;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    return Container(
      key: key == null ? Key(esError ? 'aviso_carga_error' : 'aviso_carga') : null,
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: esError ? colores.errorContainer : colores.secondaryContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        texto,
        style: TextStyle(
          color: esError ? colores.onErrorContainer : colores.onSecondaryContainer,
        ),
      ),
    );
  }
}
