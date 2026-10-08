/// El pedido armado: qué se lleva el cliente, cómo paga y cuánto es.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE ESTA PANTALLA TIENE QUE DEJAR CLARO
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **Cada renglón con su aritmética a la vista**: cantidad × precio =
///    importe. Es lo que el cliente va a revisar en el papel impreso, y si no
///    cuadra en pantalla, el vendedor lo descubre AHORA y no frente al cliente
///    con el ticket ya salido.
/// 2. **Todo es de contado** (ADR 0002 §81): el cliente paga en el acto, en
///    efectivo o por transferencia. La forma cambia lo que el vendedor entrega
///    en el corte, no los importes.
/// 3. **No hay campo de precio ni de descuento.** El vendedor no otorga
///    descuentos (ADR 0002 §7). No está deshabilitado: no existe.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_clientes.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart';
import '../../estado/carrito.dart';
import 'venta_guardada.dart';

class PantallaCarrito extends ConsumerWidget {
  const PantallaCarrito({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final carrito = ref.watch(carritoProvider);
    final cliente = ref.watch(clienteDeLaVisitaProvider);

    // La venta cerrada abre su pantalla. Se navega desde aquí y no desde el
    // botón para que el estado sea la única fuente: si la app se reconstruye
    // mientras se cobra, no se pierde el resultado.
    ref.listen(cobroProvider, (_, estado) {
      switch (estado) {
        case VentaCerrada(:final venta):
          Navigator.of(context).pushReplacement(
            MaterialPageRoute<void>(
              builder: (_) => PantallaVentaGuardada(venta: venta),
            ),
          );
        case CobroFallido(:final motivo, :final detalle):
          ScaffoldMessenger.of(context)
            ..clearSnackBars()
            ..showSnackBar(
              SnackBar(content: Text(_mensajeDeFallo(motivo, detalle))),
            );
        case CobroSinIdentidad():
          ScaffoldMessenger.of(context)
            ..clearSnackBars()
            ..showSnackBar(
              const SnackBar(
                content: Text(
                  'Este equipo no tiene folios asignados. Sincroniza una vez.',
                ),
              ),
            );
        case CobroRoto():
          // Diálogo y no aviso al pie: en los dos casos malos el vendedor TIENE
          // que leerlo antes de volver a tocar el botón, y un aviso que se va
          // en cuatro segundos no sirve para eso.
          _avisarCobroRoto(context, ref, estado);
        case CobroInactivo() || CobroEnCurso():
          break;
      }
    });

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pedido'),
        actions: [
          if (!carrito.estaVacio)
            IconButton(
              key: const Key('boton_vaciar'),
              tooltip: 'Vaciar pedido',
              icon: const Icon(Icons.delete_outline),
              onPressed: () => _confirmarVaciar(context, ref),
            ),
        ],
      ),
      bottomNavigationBar: _BarraCobro(carrito: carrito),
      body: carrito.estaVacio
          ? const Center(
              key: Key('pedido_vacio'),
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'El pedido está vacío.\nRegresa al catálogo para agregar.',
                  textAlign: TextAlign.center,
                ),
              ),
            )
          : ListView(
              padding: const EdgeInsets.only(bottom: 24),
              children: [
                if (cliente != null) _Encabezado(cliente: cliente),
                const Divider(height: 1),
                for (final linea in carrito.lineas) _RenglonLinea(linea: linea),
                const Divider(height: 1),
                _Resumen(carrito: carrito),
                _FormaDePago(carrito: carrito),
              ],
            ),
    );
  }

  /// Lo único que el vendedor necesita decidir: ¿vuelve a cobrar o no?
  ///
  /// El nombre del error no se muestra. No le dice nada a quien está frente al
  /// cliente, y el que sí importa —si la venta quedó escrita— ya viene resuelto
  /// desde el cierre.
  Future<void> _avisarCobroRoto(
    BuildContext context,
    WidgetRef ref,
    CobroRoto roto,
  ) async {
    final folio = roto.folioLocal == null ? '' : ' (folio ${roto.folioLocal})';
    final (titulo, cuerpo) = switch (roto.quedoEscrita) {
      false => (
          'No se guardó la venta',
          'El equipo falló y no quedó nada registrado: ni la venta ni la '
              'mercancía descontada. Vuelve a cobrar.',
        ),
      true => (
          'La venta SÍ quedó registrada',
          'Quedó guardada$folio, pero la pantalla del ticket no pudo abrirse. '
              'NO la vuelvas a cobrar: búscala en Mi día y, si necesita papel, '
              'reimprímela desde ahí.',
        ),
      null => (
          'No se sabe si la venta quedó',
          'El equipo falló y no se pudo averiguar si la venta$folio se '
              'guardó. ANTES de volver a cobrar, revisa Mi día: si el total ya '
              'la incluye, no la cobres otra vez.',
        ),
    };

    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        key: const Key('aviso_cobro_roto'),
        title: Text(titulo),
        content: Text(cuerpo),
        actions: [
          FilledButton(
            key: const Key('entendido_cobro_roto'),
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Entendido'),
          ),
        ],
      ),
    );
    // El estado vuelve a inactivo para que el botón quede usable: el vendedor ya
    // sabe qué pasó y qué sigue.
    ref.read(cobroProvider.notifier).reiniciar();
  }

  Future<void> _confirmarVaciar(BuildContext context, WidgetRef ref) async {
    // Se pregunta porque perder un pedido de quince renglones armado frente al
    // cliente es rehacer la visita completa.
    final confirmado = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('¿Vaciar el pedido?'),
        content: const Text('Se quitan todos los renglones. No se puede deshacer.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            key: const Key('confirmar_vaciar'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Vaciar'),
          ),
        ],
      ),
    );
    if (confirmado ?? false) {
      ref.read(carritoProvider.notifier).vaciar();
    }
  }
}

/// Cada motivo pide algo distinto del vendedor, y ninguno es un error de
/// programación: son situaciones de la calle.
String _mensajeDeFallo(MotivoNoVenta motivo, String? detalle) =>
    switch (motivo) {
      MotivoNoVenta.carritoVacio => 'No hay nada que cobrar',
      MotivoNoVenta.sinRangoDeFolios =>
        'Este equipo no tiene folios asignados. Sincroniza una vez.',
      MotivoNoVenta.sinFolios =>
        'Se acabaron los folios. Sincroniza para pedir más.',
      MotivoNoVenta.sinExistencia =>
        'Ya no hay esa mercancía en el camión${detalle == null ? '' : ': $detalle'}',
    };

class _Encabezado extends StatelessWidget {
  const _Encabezado({required this.cliente});

  final ClienteEnRuta cliente;

  @override
  Widget build(BuildContext context) => ListTile(
        leading: const Icon(Icons.storefront_outlined),
        title: Text(
          cliente.nombreComercial,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        subtitle: cliente.codigo == null ? null : Text(cliente.codigo!),
      );
}

/// Un renglón con su aritmética a la vista.
class _RenglonLinea extends ConsumerWidget {
  const _RenglonLinea({required this.linea});

  final LineaCarrito linea;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    final control = ref.read(carritoProvider.notifier);
    final p = linea.presentacion;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 8, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  p.nombre,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              Text(
                '\$${linea.importe.texto}',
                key: Key('importe_${p.llave}'),
                style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              // La operación completa, escrita: es la que el cliente revisa en
              // el papel.
              Expanded(
                child: Text(
                  '${linea.cantidad.textoCorto} ${p.unidadCodigo} '
                  '× \$${p.precio.textoCorto}',
                  key: Key('operacion_${p.llave}'),
                  style: TextStyle(fontSize: 13, color: colores.onSurfaceVariant),
                ),
              ),
              IconButton(
                key: Key('linea_menos_${p.llave}'),
                visualDensity: VisualDensity.compact,
                onPressed: () =>
                    control.fijar(p, linea.cantidad - Cantidad.deEnteros(1)),
                icon: const Icon(Icons.remove_circle_outline),
              ),
              IconButton(
                key: Key('linea_mas_${p.llave}'),
                visualDensity: VisualDensity.compact,
                onPressed: () => control.agregar(p, Cantidad.deEnteros(1)),
                icon: const Icon(Icons.add_circle_outline),
              ),
              IconButton(
                key: Key('linea_quitar_${p.llave}'),
                visualDensity: VisualDensity.compact,
                tooltip: 'Quitar',
                onPressed: () => control.quitar(p.llave),
                icon: Icon(Icons.close, color: colores.error),
              ),
            ],
          ),
          if (!p.factor.esUno)
            Text(
              // Lo que realmente sale del camión. Sin esto, "3 cajas" no le dice
              // al vendedor que se está llevando 72 piezas.
              'Salen ${linea.enUnidadesBase.textoCorto} pza del camión',
              style: TextStyle(fontSize: 11, color: colores.onSurfaceVariant),
            ),
        ],
      ),
    );
  }
}

class _Resumen extends StatelessWidget {
  const _Resumen({required this.carrito});

  final Carrito carrito;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: Row(
          children: [
            const Expanded(child: Text('Subtotal')),
            Text(
              '\$${carrito.subtotal.texto}',
              key: const Key('subtotal_pedido'),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
          ],
        ),
      );
}

/// Efectivo o transferencia. Dos botones grandes, no un menú.
///
/// La referencia de la transferencia es opcional —no siempre el cliente la tiene
/// a la mano—, pero si se escribe viaja en la venta y en el ticket: es con lo que
/// la oficina la encuentra en el estado de cuenta.
class _FormaDePago extends ConsumerStatefulWidget {
  const _FormaDePago({required this.carrito});

  final Carrito carrito;

  @override
  ConsumerState<_FormaDePago> createState() => _EstadoFormaDePago();
}

class _EstadoFormaDePago extends ConsumerState<_FormaDePago> {
  late final _referencia = TextEditingController(text: widget.carrito.referenciaPago ?? '');

  @override
  void dispose() {
    _referencia.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final control = ref.read(carritoProvider.notifier);
    final forma = widget.carrito.formaDePago;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Forma de pago', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          SegmentedButton<FormaDePago>(
            key: const Key('forma_de_pago'),
            segments: const [
              ButtonSegment(
                value: FormaDePago.efectivo,
                label: Text('Efectivo'),
                icon: Icon(Icons.payments_outlined),
              ),
              ButtonSegment(
                value: FormaDePago.transferencia,
                label: Text('Transferencia'),
                icon: Icon(Icons.account_balance_outlined),
              ),
            ],
            selected: {forma},
            onSelectionChanged: (s) =>
                control.cambiarFormaDePago(s.first, referencia: _referencia.text),
          ),
          if (forma == FormaDePago.transferencia) ...[
            const SizedBox(height: 10),
            TextField(
              key: const Key('campo_referencia_pago'),
              controller: _referencia,
              decoration: const InputDecoration(
                labelText: 'Clave de rastreo o referencia (opcional)',
                helperText: 'Con esto la oficina la encuentra en el banco. '
                    'No viene en tu efectivo del corte.',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              onChanged: (t) =>
                  control.cambiarFormaDePago(FormaDePago.transferencia, referencia: t),
            ),
          ],
        ],
      ),
    );
  }
}

/// El total y el paso siguiente.
class _BarraCobro extends ConsumerWidget {
  const _BarraCobro({required this.carrito});

  final Carrito carrito;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    final procede = !carrito.estaVacio;
    final cobrando = ref.watch(cobroProvider) is CobroEnCurso;

    return SafeArea(
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        decoration: BoxDecoration(
          color: colores.surfaceContainerHighest,
          border: Border(top: BorderSide(color: colores.outlineVariant)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text('Total',
                      style: TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
                ),
                Text(
                  '\$${carrito.total.texto}',
                  key: const Key('total_pedido'),
                  style: const TextStyle(
                    fontWeight: FontWeight.w800,
                    fontSize: 26,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                key: const Key('boton_cobrar'),
                onPressed: procede && !cobrando ? () => _cobrar(ref) : null,
                icon: cobrando
                    ? const SizedBox(
                        height: 18,
                        width: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.point_of_sale_outlined),
                label: Text(
                  carrito.formaDePago == FormaDePago.transferencia
                      ? 'Cobrar por transferencia'
                      : 'Cobrar en efectivo',
                ),
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Cobra, sellando la venta con la ubicación si el GPS responde.
  ///
  /// El GPS **no bloquea**: dentro de un mercado techado no hay satélite y la
  /// venta ocurre igual. Se le da un margen corto porque el cliente está
  /// enfrente esperando; si no llega a tiempo, la venta entra sin geosello y el
  /// servidor la marca para revisión. Esperar doce segundos con el cliente
  /// enfrente sería peor que la marca.
  Future<void> _cobrar(WidgetRef ref) async {
    final lectura = await ref
        .read(servicioUbicacionProvider)
        .leer(tiempoLimite: const Duration(seconds: 4));

    ref.read(cobroProvider.notifier).cobrar(
          ubicacion: lectura is GpsObtenido ? lectura.ubicacion : null,
        );
  }
}
