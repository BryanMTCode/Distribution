/// Modificar una venta: subir, bajar o quitar artículos (ADR 0002 §162).
///
/// «Que puedan quitar artículos de la venta, pero que se ajuste todo y el
/// inventario se devuelva a su camión. Que puedan bajar la cantidad de un
/// artículo y subirla.» Sin señal, como todo lo del vendedor.
///
/// ─────────────────────────────────────────────────────────────────────────
/// CÓMO SE VE
/// ─────────────────────────────────────────────────────────────────────────
/// Arriba, la venta como está, con «−» y «+» en cada renglón: lo que baja a cero
/// dice «se quita» y vuelve a su camión. Abajo, el catálogo con los precios de la
/// lista con que se vendió, para agregar otro artículo. Lo que sube se revisa
/// contra lo que trae el camión. Quitarlo todo no es modificar: es cancelar.
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

class PantallaModificarVenta extends ConsumerStatefulWidget {
  const PantallaModificarVenta({super.key, required this.venta});

  final VentaDeMiDia venta;

  @override
  ConsumerState<PantallaModificarVenta> createState() => _EstadoModificarVenta();
}

const _motivosDeUnToque = [
  'Me equivoqué de cantidad',
  'Se me olvidó agregarlo',
  'El cliente ya no quiso uno',
  'El cliente pidió más',
];

/// Un renglón mientras se edita. [original] es lo que llevaba la venta: cero si
/// es nuevo.
class _Renglon {
  _Renglon({this.partidaId, required this.presentacion, required this.cantidad})
      : original = partidaId == null ? Cantidad.cero : cantidad;

  final String? partidaId;
  final PresentacionVendible presentacion;
  Cantidad cantidad;
  final Cantidad original;

  Dinero get importe => importeDeLinea(cantidad, presentacion.precio);
}

class _EstadoModificarVenta extends ConsumerState<PantallaModificarVenta> {
  final _busqueda = TextEditingController();
  final _motivo = TextEditingController();
  late final List<_Renglon> _renglones = [
    for (final r in ref.read(modificacionDeVentaProvider).renglonesDe(widget.venta.id))
      _Renglon(
        partidaId: r.partidaId,
        presentacion: r.linea.presentacion,
        cantidad: r.linea.cantidad,
      ),
  ];
  String? _aviso;
  bool _guardando = false;

  /// La lista con que se vendió: lo nuevo lleva los mismos precios.
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

  bool get _cambio => _renglones.any((r) => r.cantidad != r.original);
  bool get _quedaAlgo => _renglones.any((r) => !r.cantidad.esCero);
  bool get _motivoAlcanza => _motivo.text.trim().length >= largoMinimoDelMotivo;
  Dinero get _totalNuevo => _renglones.fold(Dinero.cero, (acc, r) => acc + r.importe);

  /// Sube o baja un renglón. Lo que sube tiene que estar en el camión: lo que la
  /// venta ya llevaba de ese producto cuenta como suyo.
  void _cambiar(_Renglon r, int paso) {
    final nueva = r.cantidad + Cantidad.deEnteros(paso);
    if (nueva.esNegativa) return;
    if (paso > 0) {
      final producto = r.presentacion.productoId;
      var sube = 0;
      for (final otro in _renglones.where((x) => x.presentacion.productoId == producto)) {
        final cantidad = identical(otro, r) ? nueva : otro.cantidad;
        sube += cantidadBase(cantidad, otro.presentacion.factor).milesimos -
            cantidadBase(otro.original, otro.presentacion.factor).milesimos;
      }
      final trae = ref.read(existenciasProvider).disponible(producto);
      if (sube > trae.milesimos) {
        setState(() => _aviso = 'Ya no alcanza: el camión trae '
            '${trae.textoCorto} ${r.presentacion.unidadBaseCodigo} de '
            '${r.presentacion.nombre}.');
        return;
      }
    }
    setState(() {
      r.cantidad = nueva;
      _aviso = null;
    });
  }

  /// Agrega otro artículo: si ya va en la venta en esa presentación, le suma uno.
  void _agregarOtro(PresentacionVendible p) {
    final ya = _renglones.where((r) => r.presentacion.llave == p.llave).firstOrNull;
    if (ya != null) {
      _cambiar(ya, 1);
      return;
    }
    final nuevo = _Renglon(presentacion: p, cantidad: Cantidad.cero);
    setState(() => _renglones.add(nuevo));
    _cambiar(nuevo, 1);
    if (nuevo.cantidad.esCero) setState(() => _renglones.remove(nuevo));
  }

  Future<void> _guardar() async {
    final v = widget.venta;
    final total = _totalNuevo;
    final si = await showDialog<bool>(
      context: context,
      builder: (contexto) => AlertDialog(
        key: const Key('dialogo_confirmar_cambios'),
        title: Text('¿Guardar los cambios a ${v.folio}?'),
        content: Text(
          'El total queda en \$${total.texto} (antes \$${v.total.texto}). Lo que '
          'bajaste o quitaste vuelve a tu camión. Después imprime el ticket nuevo y '
          'recoge el de antes.',
        ),
        actions: [
          TextButton(
            key: const Key('no_guardar_cambios'),
            onPressed: () => Navigator.of(contexto).pop(false),
            child: const Text('No'),
          ),
          FilledButton(
            key: const Key('confirmar_guardar_cambios'),
            onPressed: () => Navigator.of(contexto).pop(true),
            child: const Text('Guardar'),
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
      final nuevo = ref.read(modificacionDeVentaProvider).modificar(
            v.id,
            renglones: [
              for (final r in _renglones)
                if (!r.cantidad.esCero)
                  RenglonDeVenta(
                    partidaId: r.partidaId,
                    linea: LineaCarrito(presentacion: r.presentacion, cantidad: r.cantidad),
                  ),
            ],
            motivo: _motivo.text,
            ubicacion: ubicacion,
          );
      if (!mounted) return;
      Navigator.of(context).pop(nuevo);
    } on CambioRechazado catch (e) {
      setState(() {
        _guardando = false;
        _aviso = switch (e.motivo) {
          MotivoNoSeToca.sinExistencia => 'No alcanza en el camión: ${e.detalle ?? ''}',
          MotivoNoSeToca.faltaMotivo => 'Escribe por qué la modificas.',
          MotivoNoSeToca.sinRenglones => 'Para quitarlo todo, cancela la venta.',
          MotivoNoSeToca.sinCambios => 'No cambiaste nada.',
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
    final enLaVenta = {for (final r in _renglones) r.presentacion.llave};
    final catalogo = _lista == null
        ? const <ProductoEnCatalogo>[]
        : ref.read(repoCatalogoProvider).paraCliente(
              listaPreciosId: _lista,
              busqueda: _busqueda.text,
            );
    final puede = _cambio && _quedaAlgo && _motivoAlcanza && !_guardando;

    return Scaffold(
      appBar: AppBar(title: Text('Modificar ${v.folio}')),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _cambio
                    ? 'Total \$${v.total.texto} → \$${_totalNuevo.texto}'
                    : 'Total \$${v.total.texto}',
                key: const Key('resumen_cambio'),
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              if (!_quedaAlgo)
                Text(
                  'Para quitarlo todo, cancela la venta.',
                  key: const Key('aviso_quitar_todo'),
                  style: TextStyle(color: colores.error),
                ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  key: const Key('guardar_cambios'),
                  onPressed: puede ? _guardar : null,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('Guardar cambios'),
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
          Text(
            'Lo que bajes o quites vuelve a tu camión.',
            style: TextStyle(fontSize: 12, color: colores.outline),
          ),
          const SizedBox(height: 8),
          for (final r in _renglones)
            _RenglonEditable(
              renglon: r,
              alSumar: () => _cambiar(r, 1),
              alRestar: () => _cambiar(r, -1),
            ),
          if (_aviso != null) ...[
            const SizedBox(height: 8),
            Text(_aviso!, key: const Key('aviso_cambio'), style: TextStyle(color: colores.error)),
          ],
          const SizedBox(height: 16),
          Text('Agregar otro artículo', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          TextField(
            key: const Key('buscar_para_agregar'),
            controller: _busqueda,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Buscar el artículo',
              border: OutlineInputBorder(),
            ),
          ),
          for (final producto in catalogo)
            for (final p in producto.presentaciones)
              if (!enLaVenta.contains(p.llave))
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(p.nombre),
                  subtitle: Text(
                    '${p.unidadCodigo} · \$${p.precio.textoCorto}'
                    '${producto.agotado ? ' · agotado' : ''}',
                  ),
                  trailing: IconButton(
                    key: Key('agregar_otro_${p.llave}'),
                    onPressed: producto.agotado ? null : () => _agregarOtro(p),
                    icon: const Icon(Icons.add_circle_outline),
                  ),
                ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 6,
            runSpacing: 4,
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
            key: const Key('motivo_modificar'),
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

class _RenglonEditable extends StatelessWidget {
  const _RenglonEditable({
    required this.renglon,
    required this.alSumar,
    required this.alRestar,
  });

  final _Renglon renglon;
  final VoidCallback alSumar;
  final VoidCallback alRestar;

  @override
  Widget build(BuildContext context) {
    final r = renglon;
    final llave = r.presentacion.llave;
    final colores = Theme.of(context).colorScheme;
    final estado = r.cantidad.esCero
        ? ' · se quita'
        : r.partidaId == null
            ? ' · nuevo'
            : r.cantidad != r.original
                ? ' · antes ${r.original.textoCorto}'
                : '';
    return ListTile(
      key: Key('renglon_$llave'),
      dense: true,
      contentPadding: EdgeInsets.zero,
      title: Text(
        r.presentacion.nombre,
        style: r.cantidad.esCero
            ? TextStyle(decoration: TextDecoration.lineThrough, color: colores.outline)
            : null,
      ),
      subtitle: Text('${r.presentacion.unidadCodigo} · \$${r.presentacion.precio.textoCorto}$estado'),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            key: Key('menos_$llave'),
            onPressed: r.cantidad.esCero ? null : alRestar,
            icon: const Icon(Icons.remove_circle_outline),
          ),
          Text(
            r.cantidad.textoCorto,
            key: Key('cantidad_$llave'),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          IconButton(
            key: Key('mas_$llave'),
            onPressed: alSumar,
            icon: const Icon(Icons.add_circle_outline),
          ),
        ],
      ),
    );
  }
}
