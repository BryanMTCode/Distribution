/// El pedido armado: qué se lleva el cliente, cómo paga y cuánto es.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LO QUE ESTA PANTALLA TIENE QUE DEJAR CLARO
/// ─────────────────────────────────────────────────────────────────────────
/// 1. **Cada renglón con su aritmética a la vista**: cantidad × precio =
///    importe. Es lo que el cliente va a revisar en el papel impreso, y si no
///    cuadra en pantalla, el vendedor lo descubre AHORA y no frente al cliente
///    con el ticket ya salido.
/// 2. **La forma de pago cambia lo que se puede hacer, no los importes.** De
///    contado siempre procede. A crédito puede bloquearse, y entonces se dice
///    cuánto falta abonar — no "operación no permitida".
/// 3. **No hay campo de precio ni de descuento.** El vendedor no otorga
///    descuentos (ADR 0002 §7). No está deshabilitado: no existe.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_clientes.dart';
import '../../estado/carrito.dart';

class PantallaCarrito extends ConsumerWidget {
  const PantallaCarrito({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final carrito = ref.watch(carritoProvider);
    final cliente = ref.watch(clienteDeLaVisitaProvider);
    final evaluacion = ref.watch(evaluacionProvider);

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
      bottomNavigationBar: _BarraCobro(
        carrito: carrito,
        evaluacion: evaluacion,
      ),
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
                if (evaluacion != null)
                  _AvisoCredito(evaluacion: evaluacion, aCredito: carrito.aCredito),
              ],
            ),
    );
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

/// Contado o crédito. Dos botones grandes, no un menú.
class _FormaDePago extends ConsumerWidget {
  const _FormaDePago({required this.carrito});

  final Carrito carrito;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cliente = ref.watch(clienteDeLaVisitaProvider);
    final control = ref.read(carritoProvider.notifier);
    // Un cliente sin línea de crédito no debe poder ni elegirlo: ofrecer una
    // opción que va a fallar es hacerle perder tiempo frente al cliente.
    final puedeCredito =
        cliente?.credito.permiteCredito ?? false;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Forma de pago',
              style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          SegmentedButton<bool>(
            key: const Key('forma_de_pago'),
            segments: [
              const ButtonSegment(
                value: false,
                label: Text('Contado'),
                icon: Icon(Icons.payments_outlined),
              ),
              ButtonSegment(
                value: true,
                label: const Text('Crédito'),
                icon: const Icon(Icons.account_balance_wallet_outlined),
                enabled: puedeCredito,
              ),
            ],
            selected: {carrito.aCredito},
            onSelectionChanged: (s) =>
                control.cambiarFormaDePago(aCredito: s.first),
          ),
          if (!puedeCredito)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                'Este cliente es solo de contado.',
                key: const Key('nota_solo_contado'),
                style: TextStyle(
                  fontSize: 12,
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Lo que el crédito del cliente permite, con el número que hace falta.
class _AvisoCredito extends StatelessWidget {
  const _AvisoCredito({required this.evaluacion, required this.aCredito});

  final ResultadoCredito evaluacion;
  final bool aCredito;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    if (!aCredito) return const SizedBox.shrink();

    if (evaluacion.permitida) {
      return _Tarjeta(
        clave: 'credito_ok',
        fondo: colores.tertiaryContainer,
        frente: colores.onTertiaryContainer,
        icono: Icons.check_circle_outline,
        titulo: 'Procede a crédito',
        detalle: 'Le quedarían \$${evaluacion.disponible.texto} de línea.',
      );
    }

    final (titulo, detalle) = switch (evaluacion.motivo) {
      MotivoCredito.excedeLimite => (
          'Se pasa del límite',
          // El número que resuelve la situación: cuánto tiene que abonar para
          // que la venta pase. "No permitido" deja al vendedor sin salida.
          'Necesita abonar \$${evaluacion.excedente.texto} '
              'para que pase esta venta, o cóbrale de contado.',
        ),
      MotivoCredito.clienteBloqueado => (
          'Cliente bloqueado',
          'La oficina bloqueó su crédito. De contado sí puedes venderle.',
        ),
      MotivoCredito.sinLineaDeCredito => (
          'Sin línea de crédito',
          'Este cliente solo compra de contado.',
        ),
      _ => ('No procede a crédito', 'Cóbrale de contado.'),
    };

    return _Tarjeta(
      clave: 'credito_bloquea',
      fondo: colores.errorContainer,
      frente: colores.onErrorContainer,
      icono: Icons.block_outlined,
      titulo: titulo,
      detalle: detalle,
    );
  }
}

class _Tarjeta extends StatelessWidget {
  const _Tarjeta({
    required this.clave,
    required this.fondo,
    required this.frente,
    required this.icono,
    required this.titulo,
    required this.detalle,
  });

  final String clave;
  final Color fondo;
  final Color frente;
  final IconData icono;
  final String titulo;
  final String detalle;

  @override
  Widget build(BuildContext context) => Container(
        key: Key(clave),
        margin: const EdgeInsets.fromLTRB(16, 8, 16, 8),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: fondo,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            Icon(icono, color: frente),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(titulo,
                      style: TextStyle(color: frente, fontWeight: FontWeight.w700)),
                  Text(detalle, style: TextStyle(color: frente, fontSize: 13)),
                ],
              ),
            ),
          ],
        ),
      );
}

/// El total y el paso siguiente.
class _BarraCobro extends ConsumerWidget {
  const _BarraCobro({required this.carrito, required this.evaluacion});

  final Carrito carrito;
  final ResultadoCredito? evaluacion;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    final procede = !carrito.estaVacio && (evaluacion?.permitida ?? false);

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
                key: const Key('boton_revisar_venta'),
                onPressed:
                    procede ? () => _mostrarResumen(context, ref, carrito) : null,
                icon: const Icon(Icons.receipt_long_outlined),
                label: const Text('Revisar venta'),
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

  /// Lo que se va a escribir cuando exista la Parte 2.
  ///
  /// El botón no dice "Cobrar" porque todavía no cobra: la venta —folio,
  /// descuento de inventario, sobre en la cola, ticket— es la siguiente entrega.
  /// Un botón que dijera "Cobrar" y no cobrara sería una mentira en la interfaz.
  /// Este resumen sirve para revisar los números en el teléfono, que es lo que
  /// hace falta ahora.
  void _mostrarResumen(BuildContext context, WidgetRef ref, Carrito carrito) {
    final cliente = ref.read(clienteDeLaVisitaProvider);
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 32),
        child: Column(
          key: const Key('resumen_venta'),
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Resumen de la venta',
                style: Theme.of(ctx).textTheme.titleLarge),
            const SizedBox(height: 12),
            _Dato('Cliente', cliente?.nombreComercial ?? '—'),
            _Dato('Forma de pago', carrito.aCredito ? 'Crédito' : 'Contado'),
            _Dato('Renglones', carrito.cuantasLineas.toString()),
            _Dato('Total', '\$${carrito.total.texto}'),
            const Divider(height: 24),
            Text(
              'La venta todavía no se guarda. El folio, el descuento del '
              'inventario del camión, la cola de sincronización y la remisión '
              'impresa son la siguiente entrega.',
              style: Theme.of(ctx).textTheme.bodySmall,
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () => Navigator.of(ctx).pop(),
                child: const Text('Entendido'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Dato extends StatelessWidget {
  const _Dato(this.etiqueta, this.valor);

  final String etiqueta;
  final String valor;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          children: [
            Expanded(child: Text(etiqueta)),
            Text(valor, style: const TextStyle(fontWeight: FontWeight.w700)),
          ],
        ),
      );
}
