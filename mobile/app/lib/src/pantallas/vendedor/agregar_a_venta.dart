/// Agregarle a una venta lo que le faltó (ADR 0002 §162).
///
/// «Si les faltó poner uno de un artículo, que lo agreguen y puedan imprimir el
/// nuevo ticket.» Sin señal, como todo lo del vendedor.
///
/// ─────────────────────────────────────────────────────────────────────────
/// SOLO SE AGREGA
/// ─────────────────────────────────────────────────────────────────────────
/// Lo que ya lleva la venta se ve y no se toca. Lo que se agrega sale del camión
/// y se revisa contra lo que trae, con los precios de la lista con que se vendió.
/// Bajar o quitar no se puede desde aquí: «regresaría» mercancía al camión sin
/// que nadie la viera. Para eso, se cancela la venta y se hace otra vez.
///
/// Pide el motivo, que va a la pista junto con si el ticket ya se había impreso
/// y dónde estaba el vendedor: la oficina lo ve en «Cambios de los vendedores».
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_catalogo.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart' show servicioUbicacionProvider;
import '../../estado/carrito.dart';
import '../../estado/mi_dia.dart';
import '../../estado/sesion.dart';
import 'catalogo.dart' show mensajeDeRechazoDelCarrito;

class PantallaAgregarAVenta extends ConsumerStatefulWidget {
  const PantallaAgregarAVenta({super.key, required this.venta});

  final VentaDeMiDia venta;

  @override
  ConsumerState<PantallaAgregarAVenta> createState() => _EstadoAgregarAVenta();
}

const _motivosDeUnToque = ['Se me olvidó agregarlo', 'El cliente pidió más'];

class _EstadoAgregarAVenta extends ConsumerState<PantallaAgregarAVenta> {
  final _busqueda = TextEditingController();
  final _motivo = TextEditingController();

  /// Lo que se va a agregar. Un carrito aparte, en memoria: no toca el de la
  /// visita ni su borrador.
  Carrito _agregado = const Carrito();
  String? _aviso;
  bool _guardando = false;

  /// La lista con que se vendió: lo agregado lleva los mismos precios.
  late final String? _lista = _listaDeLaVenta();

  String? _listaDeLaVenta() {
    final filas = ref.read(baseLocalProvider).db.select(
      'SELECT cliente_id, lista_precios_id FROM ventas WHERE id = ?',
      [widget.venta.id],
    );
    if (filas.isEmpty) return null;
    return filas.single['lista_precios_id'] as String? ??
        ref.read(repoCatalogoProvider).listaParaCliente(filas.single['cliente_id'] as String)?.id;
  }

  @override
  void dispose() {
    _busqueda.dispose();
    _motivo.dispose();
    super.dispose();
  }

  bool get _motivoAlcanza => _motivo.text.trim().length >= largoMinimoDelMotivo;

  void _cambiar(PresentacionVendible p, int paso) {
    final actual = _agregado.cantidadDe(p.llave);
    final nueva = actual + Cantidad.deEnteros(paso);
    final r = _agregado.fijarCantidad(
      p,
      nueva.milesimos <= 0 ? Cantidad.cero : nueva,
      existencias: ref.read(existenciasProvider),
    );
    setState(() {
      if (r.aceptado) {
        _agregado = r.carrito;
        _aviso = null;
      } else {
        _aviso = mensajeDeRechazoDelCarrito(r);
      }
    });
  }

  Future<void> _guardar() async {
    final v = widget.venta;
    final nuevoTotal = v.total + _agregado.total;
    final si = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        key: const Key('dialogo_confirmar_agregado'),
        title: Text('¿Agregar a ${v.folio}?'),
        content: Text(
          'Se agrega \$${_agregado.total.texto}. El total queda en '
          '\$${nuevoTotal.texto}. Después imprime el ticket nuevo y recoge el de antes.',
        ),
        actions: [
          TextButton(
            key: const Key('no_agregar'),
            onPressed: () => Navigator.of(contexto).pop(false),
            child: const Text('No'),
          ),
          FilledButton(
            key: const Key('confirmar_agregar'),
            onPressed: () => Navigator.of(contexto).pop(true),
            child: const Text('Agregar'),
          ),
        ],
      ),
    );
    if (si != true || !mounted) return;

    setState(() => _guardando = true);
    // Dónde estaba, para la pista. Corto: el cliente espera, y sin GPS se guarda
    // igual.
    Ubicacion? ubicacion;
    try {
      final lectura = await ref
          .read(servicioUbicacionProvider)
          .leer(tiempoLimite: const Duration(seconds: 4));
      if (lectura is GpsObtenido) ubicacion = lectura.ubicacion;
    } on Object {
      ubicacion = null;
    }
    if (!mounted) return;

    try {
      final total = ref.read(agregadoAVentaProvider).agregar(
            v.id,
            lineas: _agregado.lineas,
            motivo: _motivo.text,
            ubicacion: ubicacion,
          );
      if (!mounted) return;
      Navigator.of(context).pop(total);
    } on CambioRechazado catch (e) {
      setState(() {
        _guardando = false;
        _aviso = switch (e.motivo) {
          MotivoNoSeToca.sinExistencia => 'No alcanza en el camión: ${e.detalle ?? ''}',
          MotivoNoSeToca.faltaMotivo => 'Escribe por qué la modificas.',
          MotivoNoSeToca.sinRenglones => 'Agrega al menos un artículo.',
          MotivoNoSeToca.diaCortado => 'Ya hiciste el corte de ese día: pídeselo a la oficina.',
          MotivoNoSeToca.otroDia => 'Es de un día que ya pasó: pídeselo a la oficina.',
          MotivoNoSeToca.yaCancelada => 'Esa venta ya está cancelada.',
          MotivoNoSeToca.noExiste => 'Esa venta ya no está en el teléfono.',
        };
      });
    } on Object catch (e) {
      setState(() {
        _guardando = false;
        _aviso = 'No se pudo guardar: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.watch(revisionDelCamionProvider);
    final v = widget.venta;
    final colores = Theme.of(context).colorScheme;
    final productos = _lista == null
        ? const <ProductoEnCatalogo>[]
        : ref.read(repoCatalogoProvider).paraCliente(
              listaPreciosId: _lista,
              busqueda: _busqueda.text,
            );
    final puede = !_agregado.estaVacio && _motivoAlcanza && !_guardando;

    return Scaffold(
      appBar: AppBar(title: Text('Agregar a ${v.folio}')),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _agregado.estaVacio
                    ? 'Lleva \$${v.total.texto}'
                    : 'Se agrega \$${_agregado.total.texto} · total nuevo '
                        '\$${(v.total + _agregado.total).texto}',
                key: const Key('resumen_agregado'),
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  key: const Key('guardar_agregado'),
                  onPressed: puede ? _guardar : null,
                  icon: const Icon(Icons.add_shopping_cart),
                  label: const Text('Agregar a la venta'),
                ),
              ),
            ],
          ),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(v.cliente, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          Text('Ya lleva:', style: TextStyle(color: colores.outline)),
          for (final p in v.partidas)
            Text('${p.cantidadTexto} ${p.unidad} · ${p.producto}'),
          const SizedBox(height: 8),
          Text(
            'Solo se puede agregar. Para bajar o quitar algo, cancela la venta y '
            'hazla otra vez.',
            style: TextStyle(fontSize: 12, color: colores.outline),
          ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('buscar_agregar'),
            controller: _busqueda,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Buscar el artículo',
              border: OutlineInputBorder(),
            ),
          ),
          if (_aviso != null) ...[
            const SizedBox(height: 8),
            Text(_aviso!, key: const Key('aviso_agregado'), style: TextStyle(color: colores.error)),
          ],
          const SizedBox(height: 8),
          for (final producto in productos)
            for (final p in producto.presentaciones)
              _RenglonParaAgregar(
                presentacion: p,
                cantidad: _agregado.cantidadDe(p.llave),
                agotado: producto.agotado,
                alSumar: () => _cambiar(p, 1),
                alRestar: () => _cambiar(p, -1),
              ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 6,
            children: [
              for (final m in _motivosDeUnToque)
                ActionChip(
                  label: Text(m),
                  onPressed: () => setState(() => _motivo.text = m),
                ),
            ],
          ),
          const SizedBox(height: 8),
          TextField(
            key: const Key('motivo_agregar'),
            controller: _motivo,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              labelText: '¿Por qué la modificas?',
              border: OutlineInputBorder(),
            ),
          ),
        ],
      ),
    );
  }
}

class _RenglonParaAgregar extends StatelessWidget {
  const _RenglonParaAgregar({
    required this.presentacion,
    required this.cantidad,
    required this.agotado,
    required this.alSumar,
    required this.alRestar,
  });

  final PresentacionVendible presentacion;
  final Cantidad cantidad;
  final bool agotado;
  final VoidCallback alSumar;
  final VoidCallback alRestar;

  @override
  Widget build(BuildContext context) {
    final llave = presentacion.llave;
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      title: Text(presentacion.nombre),
      subtitle: Text(
        '${presentacion.unidadCodigo} · \$${presentacion.precio.textoCorto}'
        '${agotado ? ' · agotado' : ''}',
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            key: Key('menos_agregar_$llave'),
            onPressed: cantidad.esCero ? null : alRestar,
            icon: const Icon(Icons.remove_circle_outline),
          ),
          Text(
            cantidad.textoCorto,
            key: Key('cantidad_agregar_$llave'),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          IconButton(
            key: Key('mas_agregar_$llave'),
            onPressed: agotado ? null : alSumar,
            icon: const Icon(Icons.add_circle_outline),
          ),
        ],
      ),
    );
  }
}
