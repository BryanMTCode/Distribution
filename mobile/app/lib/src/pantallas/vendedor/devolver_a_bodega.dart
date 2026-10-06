/// «Devolver a la bodega»: lo que bajo del camión y entrego.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ LA CAPTURA EL VENDEDOR, Y SIN SEÑAL
/// ─────────────────────────────────────────────────────────────────────────
/// Él es el dueño del camión y el único que sabe que acaba de bajar 18 cajas.
/// Exigirle conexión para registrarlo haría que lo apuntara en papel. Es el mismo
/// trato que una merma, por la misma razón.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE ENTREGA NO ES LO QUE DEJA DE DEBER
/// ─────────────────────────────────────────────────────────────────────────
/// La mercancía sale de su camión en el momento en que captura esto —eso ya pasó—
/// pero **no entra a la bodega hasta que alguien la cuenta**. En medio está en
/// tránsito. Esta pantalla lo dice con esas palabras, porque la alternativa es que
/// el vendedor crea que ya quedó libre y se entere en la liquidación de que
/// contaron 16 de las 18 que dejó.
///
/// Por eso la lista de abajo no dice «enviado»: dice qué contaron, cuando ya hay
/// quien lo haya contado. Es su comprobante.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA EXISTENCIA SE MUESTRA Y NO ES TOPE
/// ─────────────────────────────────────────────────────────────────────────
/// Igual que la merma (§0.1). Si él dice que bajó 18, bajó 18: que el conteo del
/// teléfono diga 12 no cambia el hecho físico, y rechazarlo no devolvería la
/// mercancía al camión — solo haría que dejara de registrar devoluciones.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_catalogo.dart';
import '../../estado/mermas.dart';
import '../../estado/traspasos.dart';
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
      return cantidadBase(Cantidad.deTexto('$enteros.$decimales'), unidad.factor);
    } on FormatException {
      return null;
    }
  }

  void liberar() => cuantas.dispose();
}

class PantallaDevolverABodega extends ConsumerStatefulWidget {
  const PantallaDevolverABodega({super.key});

  @override
  ConsumerState<PantallaDevolverABodega> createState() => _EstadoDevolver();
}

class _EstadoDevolver extends ConsumerState<PantallaDevolverABodega> {
  final _observaciones = TextEditingController();
  final _busqueda = TextEditingController();
  final List<_EnCaptura> _renglones = [];
  String? _error;

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

  /// Llena los renglones con TODO lo que el camión trae, al saldo que trae.
  ///
  /// Es el caso normal del fin de ruta —se baja todo— y capturarlo producto por
  /// producto con treinta renglones es lo que hace que nadie lo capture.
  void _bajarTodo(List<ProductoDelCamion> todos) {
    setState(() {
      for (final p in todos) {
        if (p.existenciaBase.milesimos <= 0) continue;
        if (_renglones.any((r) => r.producto.id == p.id)) continue;
        final renglon = _EnCaptura(p);
        // En unidad base, no en cajas: el saldo puede traer piezas sueltas que no
        // completan una caja, y convertirlo a cajas las perdería.
        renglon.unidad = p.unidades.firstWhere(
          (u) => u.factor.diezmilesimos == Factor.uno.diezmilesimos,
          orElse: () => p.porOmision,
        );
        renglon.cuantas.text = p.existenciaBase.texto;
        _renglones.add(renglon);
      }
      _busqueda.clear();
    });
  }

  void _registrar() {
    if (_renglones.isEmpty) {
      setState(() => _error = 'Escoge al menos un producto.');
      return;
    }

    final renglones = <RenglonDeTraspaso>[];
    for (final r in _renglones) {
      final cantidad = r.enBase;
      if (cantidad == null || cantidad.milesimos <= 0) {
        setState(() => _error = 'Pon cuántas bajaste de ${r.producto.nombre}.');
        return;
      }
      renglones.add(
        RenglonDeTraspaso(productoId: r.producto.id, cantidadBase: cantidad),
      );
    }

    setState(() => _error = null);
    ref.read(traspasoProvider.notifier).registrar(
          renglones: renglones,
          observaciones: _observaciones.text,
        );
  }

  @override
  Widget build(BuildContext context) {
    final estado = ref.watch(traspasoProvider);
    final recientes = ref.watch(traspasosRecientesProvider);

    if (estado is TraspasoRegistrado) {
      return _DevolucionGuardadaVista(
        traspaso: estado.traspaso,
        alTerminar: () {
          ref.read(traspasoProvider.notifier).reiniciar();
          Navigator.of(context).pop();
        },
      );
    }

    final todos = ref.watch(productosDelCamionProvider);
    final conSaldo = todos.where((p) => p.existenciaBase.milesimos > 0).toList();

    return Scaffold(
      appBar: AppBar(title: const Text('Devolver a la bodega')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Aviso(
            'Lo que captures aquí SALE de tu camión en este momento. Entra a la '
            'bodega cuando alguien lo cuente, y mientras eso pasa queda en '
            'tránsito: abajo vas a ver cuánto contaron.',
          ),
          const SizedBox(height: 20),

          Row(
            children: [
              Expanded(
                child: Text(
                  'Qué bajas',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              if (conSaldo.isNotEmpty)
                TextButton.icon(
                  key: const Key('bajar_todo'),
                  icon: const Icon(Icons.playlist_add_check),
                  label: const Text('Todo el camión'),
                  onPressed: () => _bajarTodo(conSaldo),
                ),
            ],
          ),
          const SizedBox(height: 8),
          for (final r in _renglones) _renglon(r),
          _buscador(todos),
          const SizedBox(height: 20),

          TextField(
            key: const Key('observaciones_traspaso'),
            controller: _observaciones,
            maxLines: 2,
            decoration: const InputDecoration(
              labelText: 'Nota (opcional)',
              helperText: 'Lo que la bodega va a leer cuando lo reciba.',
              border: OutlineInputBorder(),
            ),
          ),

          if (_error != null) ...[
            const SizedBox(height: 16),
            Aviso(_error!, grave: true),
          ],
          if (estado is TraspasoFallido) ...[
            const SizedBox(height: 16),
            Aviso(estado.mensaje, grave: true),
          ],
          if (estado is TraspasoSinIdentidad) ...[
            const SizedBox(height: 16),
            const Aviso(
              'Este equipo no tiene credencial o no está registrado. Vuelve a '
              'entrar con señal.',
              grave: true,
            ),
          ],

          const SizedBox(height: 24),
          FilledButton(
            key: const Key('registrar_traspaso'),
            onPressed: estado is TraspasoEnCurso ? null : _registrar,
            child: estado is TraspasoEnCurso
                ? const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Registrar la devolución'),
          ),

          if (recientes.isNotEmpty) ...[
            const SizedBox(height: 32),
            Text(
              'Lo que ya entregaste',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            for (final t in recientes) _historia(t),
          ],
        ],
      ),
    );
  }

  Widget _renglon(_EnCaptura r) {
    final capturado = r.enBase;
    final existencia = r.producto.existenciaBase;
    // Se avisa, no se bloquea: si bajó una caja que el sistema no sabía que traía,
    // bajó una caja.
    final pasaLoQueHay =
        capturado != null && capturado.milesimos > existencia.milesimos;

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
                      r.unidad =
                          r.producto.unidades.firstWhere((u) => u.codigo == v);
                    }),
                  )
                else
                  Text(r.unidad.codigo),
              ],
            ),
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
                  'Es más de lo que el camión dice que trae. Se registra igual y '
                  'la oficina lo revisa: si lo bajaste, lo bajaste.',
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buscador(List<ProductoDelCamion> todos) {
    final filtro = _busqueda.text.trim().toLowerCase();
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
          key: const Key('buscar_producto_traspaso'),
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
            child: Aviso('El camión está vacío: no hay nada que devolver.'),
          ),
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

  /// Una devolución ya entregada, con lo que la bodega contó si ya contó.
  ///
  /// La diferencia se muestra en rojo y con palabras. Enterarse en la liquidación
  /// de que contaron 16 de las 18 que dejó es lo que rompe la confianza; verlo aquí
  /// el mismo día le da tiempo de ir a preguntar.
  Widget _historia(TraspasoEnLista t) {
    final colores = Theme.of(context).colorScheme;
    final recibido = t.recibido;

    return ListTile(
      key: Key('traspaso_${t.id}'),
      dense: true,
      leading: Icon(
        t.recibida ? Icons.check_circle_outline : Icons.schedule,
        color: t.hayDiferencia ? colores.error : colores.outline,
      ),
      title: Text(t.folio ?? 'Sin folio todavía'),
      subtitle: Text(
        t.recibida
            ? (recibido == null
                ? 'Recibida · ${t.fechaOperativa}'
                : t.hayDiferencia
                    ? 'Dejaste ${t.declarado.textoCorto} y contaron '
                        '${recibido.textoCorto}'
                    : 'Contaron ${recibido.textoCorto}: cuadró')
            : 'En tránsito: nadie la ha contado · ${t.declarado.textoCorto}',
        style: TextStyle(
          color: t.hayDiferencia ? colores.error : null,
        ),
      ),
      trailing: Text(
        '${t.renglones}',
        style: TextStyle(color: colores.outline),
      ),
    );
  }
}

/// La devolución ya guardada. No hay folio todavía: lo pone el servidor.
class _DevolucionGuardadaVista extends StatelessWidget {
  const _DevolucionGuardadaVista({
    required this.traspaso,
    required this.alTerminar,
  });

  final TraspasoGuardado traspaso;
  final VoidCallback alTerminar;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Devolución registrada'),
        automaticallyImplyLeading: false,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Center(
            child: Column(
              children: [
                const Icon(Icons.local_shipping_outlined, size: 56),
                const SizedBox(height: 8),
                Text(
                  '${traspaso.totalBase.textoCorto} en '
                  '${traspaso.renglones.length} producto'
                  '${traspaso.renglones.length == 1 ? '' : 's'}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          const Text(
            'Ya salió de tu camión y está en tránsito. El folio lo pone el '
            'servidor cuando sincronices, y cuando alguien la cuente en la bodega '
            'vas a ver aquí cuánto contaron.',
          ),
          const SizedBox(height: 12),
          const Aviso(
            'Si lo que contaron no coincide con lo que dejaste, la diferencia se '
            'queda registrada: no desaparece ni se te cobra en silencio.',
          ),
          const SizedBox(height: 24),
          FilledButton(
            key: const Key('traspaso_listo'),
            onPressed: alTerminar,
            child: const Text('Listo'),
          ),
        ],
      ),
    );
  }
}
