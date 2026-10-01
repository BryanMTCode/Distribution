/// Registrar lo que se perdió, o lo que el cliente devolvió.
///
/// ─────────────────────────────────────────────────────────────────────────
/// ESTA PANTALLA EXISTE PARA QUE NO SE LE COBRE AL VENDEDOR
/// ─────────────────────────────────────────────────────────────────────────
/// La liquidación resta la merma del esperado:
///
///     esperado = cargado − vendido − merma + devuelto
///
/// Si una caja se revienta en el camión y nadie la registra, al cierre falta
/// mercancía que el sistema no puede explicar, y ese faltante se le carga al
/// vendedor. Es el único caso que no puede corregir después: para el cierre, el
/// cartón roto ya se tiró.
///
/// Por eso la captura tiene que ser de treinta segundos, y por eso **nada aquí
/// bloquea**.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA EXISTENCIA DEL CAMIÓN SE MUESTRA, PERO NO ES TOPE
/// ─────────────────────────────────────────────────────────────────────────
/// Aquí la regla es la OPUESTA a la del carrito. La venta exige existencia porque
/// la mercancía todavía no cambió de manos y el vendedor puede revisar. La merma
/// no la exige porque el cartón ya está roto: si el camión marca 2 y se rompieron
/// 3, el que está mal es el conteo, no el mundo.
///
/// La pantalla muestra lo que el camión dice que queda —sirve para notar un
/// dedazo— y deja capturar más. El servidor lo marca para revisión y lo registra
/// (§0.1).
///
/// ─────────────────────────────────────────────────────────────────────────
/// SE CAPTURA EN CAJAS Y SE GUARDA EN PIEZAS
/// ─────────────────────────────────────────────────────────────────────────
/// Igual que en la carga. El vendedor ve "2 CAJA" porque así se rompió, y lo que
/// viaja son 48 piezas: es la regla que evita que media empresa cuente cajas y la
/// otra media piezas.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_catalogo.dart';
import '../../datos/repo_clientes.dart';
import '../../estado/mermas.dart';
import 'comunes.dart';

/// Un renglón a medio capturar: producto, presentación y cuántas.
class _EnCaptura {
  _EnCaptura(this.producto)
      : unidad = producto.porOmision,
        cuantas = TextEditingController();

  final ProductoDelCamion producto;
  UnidadDelProducto unidad;
  final TextEditingController cuantas;

  /// Lo capturado, convertido a unidad base. `null` cuando no es un número.
  ///
  /// La conversión pasa por enteros: `Cantidad` son milésimos y `Factor` son
  /// diezmilésimos, y ningún `double` decide el resultado.
  Cantidad? get enBase {
    final texto = cuantas.text.trim().replaceAll(',', '');
    if (texto.isEmpty) return null;
    final partes = texto.split('.');
    if (partes.length > 2) return null;
    final enteros = partes.first.isEmpty ? '0' : partes.first;
    final decimales = (partes.length == 2 ? partes[1] : '').padRight(3, '0');
    if (decimales.length > 3 ||
        !RegExp(r'^\d+$').hasMatch(enteros) ||
        !RegExp(r'^\d*$').hasMatch(decimales)) {
      return null;
    }
    try {
      return cantidadBase(
        Cantidad.deTexto('$enteros.$decimales'),
        unidad.factor,
      );
    } on FormatException {
      return null;
    }
  }

  void liberar() => cuantas.dispose();
}

class PantallaMerma extends ConsumerStatefulWidget {
  const PantallaMerma({super.key, this.cliente});

  /// Cuando viene de la visita a un cliente, la pantalla abre en modo
  /// **devolución**: es de donde sale la mercancía que regresa.
  final ClienteEnRuta? cliente;

  @override
  ConsumerState<PantallaMerma> createState() => _EstadoMerma();
}

class _EstadoMerma extends ConsumerState<PantallaMerma> {
  final _observaciones = TextEditingController();
  final _busqueda = TextEditingController();
  final List<_EnCaptura> _renglones = [];
  late TipoDeMerma _tipo;
  String? _motivo;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tipo = widget.cliente == null
        ? TipoDeMerma.merma
        : TipoDeMerma.devolucion;
  }

  @override
  void dispose() {
    for (final r in _renglones) {
      r.liberar();
    }
    _observaciones.dispose();
    _busqueda.dispose();
    super.dispose();
  }

  void _agregar(ProductoDelCamion producto) {
    setState(() {
      // Si ya está, no se duplica el renglón: se deja que el vendedor corrija el
      // número que ya escribió. El dominio sabe sumar dos renglones del mismo
      // producto, pero dos campos para lo mismo en pantalla solo confunden.
      final existente =
          _renglones.where((r) => r.producto.id == producto.id).firstOrNull;
      if (existente != null) return;
      _renglones.add(_EnCaptura(producto));
      _busqueda.clear();
    });
  }

  void _quitar(_EnCaptura renglon) {
    setState(() {
      _renglones.remove(renglon);
      renglon.liberar();
    });
  }

  void _registrar() {
    if (_motivo == null) {
      setState(() => _error = 'Escoge por qué se perdió.');
      return;
    }
    if (_renglones.isEmpty) {
      setState(() => _error = 'Escoge al menos un producto.');
      return;
    }

    final renglones = <RenglonDeMerma>[];
    for (final r in _renglones) {
      final cantidad = r.enBase;
      if (cantidad == null || cantidad.milesimos <= 0) {
        setState(
          () => _error = 'Pon cuántas se perdieron de ${r.producto.nombre}.',
        );
        return;
      }
      renglones.add(
        RenglonDeMerma(productoId: r.producto.id, cantidadBase: cantidad),
      );
    }

    setState(() => _error = null);
    ref.read(mermaProvider.notifier).registrar(
          tipo: _tipo,
          motivoCodigo: _motivo!,
          renglones: renglones,
          clienteId: widget.cliente?.id,
          observaciones: _observaciones.text,
        );
  }

  @override
  Widget build(BuildContext context) {
    final estado = ref.watch(mermaProvider);
    final motivos = ref.watch(motivosDeMermaProvider);
    final folios = ref.watch(foliosDeMermaProvider);

    if (estado is MermaRegistrada) {
      return _MermaGuardadaVista(
        merma: estado.merma,
        alTerminar: () {
          ref.read(mermaProvider.notifier).reiniciar();
          Navigator.of(context).pop();
        },
      );
    }

    // Un selector vacío parece una falla de la aplicación. Decirlo con palabras
    // es lo que lleva al vendedor a sincronizar, que es lo que lo arregla.
    if (motivos.isEmpty) {
      return Scaffold(
        appBar: AppBar(title: const Text('Registrar merma')),
        body: const Padding(
          padding: EdgeInsets.all(24),
          child: Aviso(
            'Este equipo todavía no recibió el catálogo de motivos. Sincroniza '
            'con señal y vuelve a entrar: sin motivos no se puede registrar una '
            'merma, y la pérdida quedaría como faltante tuyo.',
            grave: true,
          ),
        ),
      );
    }

    final esDevolucion = _tipo == TipoDeMerma.devolucion;

    return Scaffold(
      appBar: AppBar(
        title: Text(esDevolucion ? 'Devolución del cliente' : 'Registrar merma'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (widget.cliente != null) ...[
            Text(
              widget.cliente!.nombreComercial,
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 12),
          ],

          // El signo, dicho con el efecto y no con el nombre del tipo: "sale del
          // camión" se entiende sin saber qué es una merma.
          SegmentedButton<TipoDeMerma>(
            key: const Key('tipo_de_merma'),
            segments: const [
              ButtonSegment(
                value: TipoDeMerma.merma,
                label: Text('Se perdió'),
                icon: Icon(Icons.delete_outline),
              ),
              ButtonSegment(
                value: TipoDeMerma.devolucion,
                label: Text('Me la devolvió'),
                icon: Icon(Icons.undo),
              ),
            ],
            selected: {_tipo},
            onSelectionChanged: (s) => setState(() => _tipo = s.first),
          ),
          const SizedBox(height: 6),
          Text(
            esDevolucion
                ? 'Entra al camión y regresa a la bodega en la liquidación.'
                : 'Sale del camión. Es lo que evita que el faltante se te cargue '
                    'a ti.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (esDevolucion && widget.cliente == null) ...[
            const SizedBox(height: 12),
            const Aviso(
              'Una devolución se registra desde la visita al cliente: sin él la '
              'oficina no puede revisarla contra su venta.',
              grave: true,
            ),
          ],
          const SizedBox(height: 16),

          const Text('Por qué'),
          const SizedBox(height: 6),
          DropdownButtonFormField<String>(
            key: const Key('motivo_de_merma'),
            initialValue: _motivo,
            isExpanded: true,
            decoration: const InputDecoration(border: OutlineInputBorder()),
            hint: const Text('Escoge el motivo'),
            items: [
              for (final m in motivos)
                DropdownMenuItem(value: m.codigo, child: Text(m.nombre)),
            ],
            onChanged: (v) => setState(() => _motivo = v),
          ),
          // `afecta_vendedor` lo decide la OFICINA en el catálogo, nunca el
          // vendedor al capturar. Pero se le dice: enterarse en la liquidación de
          // que ese motivo se le descuenta es lo que rompe la confianza.
          if (_motivo != null) ..._avisoDeCargo(motivos),
          const SizedBox(height: 20),

          Text('Qué', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          for (final r in _renglones) _renglon(r),
          _buscador(),
          const SizedBox(height: 20),

          TextField(
            key: const Key('observaciones_merma'),
            controller: _observaciones,
            maxLines: 2,
            decoration: const InputDecoration(
              labelText: 'Qué pasó (opcional)',
              helperText: 'Lo que la oficina va a leer si pregunta.',
              border: OutlineInputBorder(),
            ),
          ),

          if (folios != null && folios.porAgotarse) ...[
            const SizedBox(height: 16),
            Aviso(
              'Te quedan ${folios.restantes} folios de merma. Sincroniza cuando '
              'tengas señal para pedir más.',
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 16),
            Aviso(_error!, grave: true),
          ],
          if (estado is MermaFallida) ...[
            const SizedBox(height: 16),
            Aviso(estado.mensaje, grave: true),
          ],
          if (estado is MermaSinIdentidad) ...[
            const SizedBox(height: 16),
            const Aviso(
              'Este equipo no tiene credencial o no está registrado. Vuelve a '
              'entrar con señal.',
              grave: true,
            ),
          ],

          const SizedBox(height: 24),
          FilledButton(
            key: const Key('registrar_merma'),
            onPressed: estado is MermaEnCurso ? null : _registrar,
            child: estado is MermaEnCurso
                ? const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : Text(esDevolucion ? 'Registrar la devolución' : 'Registrar la merma'),
          ),
        ],
      ),
    );
  }

  List<Widget> _avisoDeCargo(List<MotivoDeMerma> motivos) {
    final motivo = motivos.where((m) => m.codigo == _motivo).firstOrNull;
    if (motivo == null || !motivo.afectaVendedor) return const [];
    return [
      const SizedBox(height: 8),
      const Aviso(
        'Con este motivo la pérdida se te descuenta en la liquidación. '
        'Regístrala igual: esconderla la convierte en un faltante sin '
        'explicación, que es peor.',
      ),
    ];
  }

  Widget _renglon(_EnCaptura r) {
    final capturado = r.enBase;
    final existencia = r.producto.existenciaBase;
    // Se avisa, no se bloquea: el cartón ya está roto.
    final pasaLoQueHay = _tipo == TipoDeMerma.merma &&
        capturado != null &&
        capturado.milesimos > existencia.milesimos;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(r.producto.nombre),
                      Text(
                        'El camión trae ${existencia.textoCorto}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
                IconButton(
                  key: Key('quitar_${r.producto.id}'),
                  icon: const Icon(Icons.close),
                  onPressed: () => _quitar(r),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    key: Key('cuantas_${r.producto.id}'),
                    controller: r.cuantas,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    inputFormatters: [
                      FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]')),
                    ],
                    decoration: const InputDecoration(
                      labelText: 'Cuántas',
                      border: OutlineInputBorder(),
                    ),
                    onChanged: (_) => setState(() {}),
                  ),
                ),
                const SizedBox(width: 8),
                if (r.producto.unidades.length > 1)
                  DropdownButton<String>(
                    key: Key('unidad_${r.producto.id}'),
                    value: r.unidad.codigo,
                    items: [
                      for (final u in r.producto.unidades)
                        DropdownMenuItem(value: u.codigo, child: Text(u.codigo)),
                    ],
                    onChanged: (v) => setState(() {
                      r.unidad = r.producto.unidades
                          .firstWhere((u) => u.codigo == v);
                    }),
                  )
                else
                  Text(r.unidad.codigo),
              ],
            ),
            // La conversión se muestra: el vendedor captura cajas y lo que viaja
            // son piezas, y ver el resultado es lo que atrapa el dedazo antes de
            // guardar.
            if (capturado != null && r.unidad.factor.diezmilesimos != 10000)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Son ${capturado.textoCorto} en piezas',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            if (pasaLoQueHay)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Aviso(
                  'Es más de lo que el camión dice que trae. Se registra igual '
                  'y la oficina lo revisa: si se rompió, se rompió.',
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buscador() {
    final filtro = _busqueda.text.trim().toLowerCase();
    final todos = ref.watch(productosDelCamionProvider);
    final yaPuestos = {for (final r in _renglones) r.producto.id};
    final candidatos = [
      for (final p in todos)
        if (!yaPuestos.contains(p.id) &&
            (filtro.isEmpty ||
                p.nombre.toLowerCase().contains(filtro) ||
                p.sku.toLowerCase().contains(filtro)))
          p,
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        TextField(
          key: const Key('buscar_producto_merma'),
          controller: _busqueda,
          decoration: const InputDecoration(
            labelText: 'Buscar producto',
            prefixIcon: Icon(Icons.search),
            border: OutlineInputBorder(),
          ),
          onChanged: (_) => setState(() {}),
        ),
        if (todos.isEmpty)
          const Padding(
            padding: EdgeInsets.only(top: 12),
            child: Aviso(
              'El camión no trae carga. Sin carga no hay nada que mermar.',
            ),
          ),
        // Se muestran pocos: la lista completa empuja el botón de registrar
        // fuera de la pantalla, y lo que se busca se escribe.
        for (final p in candidatos.take(filtro.isEmpty ? 4 : 8))
          ListTile(
            key: Key('escoger_${p.id}'),
            dense: true,
            title: Text(p.nombre),
            subtitle: Text('${p.sku} · trae ${p.existenciaBase.textoCorto}'),
            trailing: const Icon(Icons.add),
            onTap: () => _agregar(p),
          ),
      ],
    );
  }
}

/// La merma ya guardada. No hay ticket: no es un documento del cliente.
class _MermaGuardadaVista extends StatelessWidget {
  const _MermaGuardadaVista({required this.merma, required this.alTerminar});

  final MermaGuardada merma;
  final VoidCallback alTerminar;

  @override
  Widget build(BuildContext context) {
    final esDevolucion = merma.tipo == TipoDeMerma.devolucion;

    return Scaffold(
      appBar: AppBar(
        title: Text(esDevolucion ? 'Devolución registrada' : 'Merma registrada'),
        automaticallyImplyLeading: false,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Center(
            child: Column(
              children: [
                const Icon(Icons.check_circle, size: 56),
                const SizedBox(height: 8),
                Text(
                  'Folio ${merma.folioConsecutivo}',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                Text(
                  '${merma.total.textoCorto} en '
                  '${merma.renglones.length} producto'
                  '${merma.renglones.length == 1 ? '' : 's'}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Text(
            esDevolucion
                ? 'La mercancía ya cuenta como que entró al camión. En la '
                    'liquidación se suma a lo que regresas.'
                : 'Ya está en la cola. En la liquidación esto se resta de lo '
                    'que se te va a contar, así que el faltante no va a '
                    'aparecer como tuyo.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 24),
          FilledButton(
            key: const Key('merma_listo'),
            onPressed: alTerminar,
            child: const Text('Listo'),
          ),
        ],
      ),
    );
  }
}
