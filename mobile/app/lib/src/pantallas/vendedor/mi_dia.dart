/// «Mi día»: lo que vendí y el dinero que debo entregar.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTA PANTALLA EXISTE
/// ───────────────────────────────────────────────────────────────────────────
/// Hasta ahora el vendedor llegaba a la bodega sin saber cuánto debía entregar:
/// lo sabía la oficina, al abrir la liquidación. Eso convierte el arqueo en una
/// sorpresa, y una sorpresa con el dinero en la mano se discute.
///
/// El número grande es **la misma cuenta que el arqueo**: ventas de contado más
/// cobros en efectivo. Si fuera otra, esta pantalla sería una promesa que la
/// oficina no va a cumplir.
///
/// Lee de SQLite, así que funciona sin señal — que es cuando hace falta: a media
/// ruta, decidiendo si alcanza el cambio, y en el patio antes de entrar a cuadrar.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/mi_dia.dart';

class PantallaMiDia extends ConsumerWidget {
  const PantallaMiDia({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dia = ref.watch(miDiaProvider);
    final colores = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Mi día')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // ---------------------------------------------------------------
          // EL NÚMERO QUE IMPORTA, y va solo y grande.
          // ---------------------------------------------------------------
          // Es lo único que el vendedor necesita leer de reojo, con el camión
          // abierto y gente esperando. Todo lo demás es el desglose de por qué.
          Card(
            key: const Key('tarjeta_efectivo'),
            color: colores.primaryContainer,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 20),
              child: Column(
                children: [
                  Text(
                    'Efectivo que debes entregar',
                    style: TextStyle(fontSize: 15, color: colores.onPrimaryContainer),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '\$${dia.efectivo.texto}',
                    key: const Key('monto_efectivo'),
                    style: TextStyle(
                      fontSize: 42,
                      fontWeight: FontWeight.bold,
                      color: colores.onPrimaryContainer,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),

          _Renglon(
            etiqueta: 'Ventas de contado',
            monto: dia.contado,
            nota: 'dinero que trae en la bolsa',
          ),
          _Renglon(
            etiqueta: 'Cobros en efectivo',
            monto: dia.cobrosEfectivo,
            nota: 'de cuentas anteriores',
          ),
          const Divider(height: 24),

          // Lo que NO es efectivo, y se muestra justamente para que no se cuente.
          // Un vendedor que suma su crédito al dinero cree que le falta en la caja.
          _Renglon(
            etiqueta: 'Ventas a crédito',
            monto: dia.credito,
            nota: 'salió mercancía, NO entró dinero',
            apagado: true,
          ),
          if (dia.cobrosOtros != Dinero.cero)
            _Renglon(
              etiqueta: 'Cobros por transferencia',
              monto: dia.cobrosOtros,
              nota: 'entraron al sistema, no a tu bolsa',
              apagado: true,
            ),
          const Divider(height: 24),
          _Renglon(
            etiqueta: 'Total vendido hoy',
            monto: dia.vendido,
            nota: 'contado y crédito juntos',
            negrita: true,
          ),

          // ---------------------------------------------------------------
          // Lo que todavía no sabe la oficina.
          // ---------------------------------------------------------------
          if (dia.sinSincronizar > 0) ...[
            const SizedBox(height: 16),
            Card(
              key: const Key('aviso_sin_sincronizar'),
              color: colores.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  children: [
                    Icon(Icons.cloud_off_outlined, color: colores.onErrorContainer),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        '${dia.sinSincronizar} documento(s) no han subido. '
                        'Sincroniza antes de entregar: lo que no subió, la oficina '
                        'todavía no lo sabe.',
                        style: TextStyle(color: colores.onErrorContainer, fontSize: 13),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],

          // ---------------------------------------------------------------
          // LO QUE LA OFICINA TOCÓ HOY
          // ---------------------------------------------------------------
          // Una venta que gerencia canceló deja de sumar en el número de arriba, y
          // eso es correcto: el arqueo del servidor tampoco la cuenta. Pero si solo
          // bajara el total, el vendedor vería su número caer sin explicación y
          // pensaría que la app le perdió una venta — y al día siguiente apuntaría
          // en papel «por si acaso». Así que aparece, con el motivo que la oficina
          // escribió.
          if (dia.tocadasPorOficina.isNotEmpty) ...[
            const SizedBox(height: 24),
            Card(
              key: const Key('tarjeta_oficina'),
              color: colores.surfaceContainerHighest,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.edit_note_outlined, size: 20, color: colores.outline),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            dia.tocadasPorOficina.length == 1
                                ? 'La oficina cambió una venta de hoy'
                                : 'La oficina cambió '
                                    '${dia.tocadasPorOficina.length} ventas de hoy',
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Ya está descontado del efectivo de arriba. La mercancía '
                      'volvió a tu camión.',
                      style: TextStyle(fontSize: 12, color: colores.outline),
                    ),
                    for (final t in dia.tocadasPorOficina)
                      Padding(
                        key: Key('oficina_${t.folio}'),
                        padding: const EdgeInsets.only(top: 12),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    '${t.folio} · ${t.cliente}',
                                    style: const TextStyle(fontWeight: FontWeight.w600),
                                  ),
                                ),
                                Text(
                                  t.cancelada ? 'cancelada' : '\$${t.total.texto}',
                                  style: TextStyle(
                                    fontWeight: FontWeight.bold,
                                    color: t.cancelada ? colores.error : null,
                                  ),
                                ),
                              ],
                            ),
                            if (t.nota != null)
                              Text(
                                '«${t.nota}»',
                                style: TextStyle(fontSize: 12, color: colores.outline),
                              ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ],

          const SizedBox(height: 24),
          Text('Mis ventas de hoy', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),

          if (dia.ventas.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Text('Todavía no has vendido hoy.', textAlign: TextAlign.center),
            )
          else
            for (final venta in dia.ventas)
              ListTile(
                key: Key('venta_${venta.folio}'),
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  venta.sincronizada ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
                  size: 20,
                  color: venta.sincronizada ? colores.outline : colores.error,
                ),
                title: Text(venta.cliente),
                subtitle: Text(
                  '${venta.folio} · ${venta.esContado ? "contado" : "crédito"}',
                ),
                trailing: Text(
                  '\$${venta.total.texto}',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    // El crédito se ve distinto en la lista por lo mismo que en el
                    // desglose: no es dinero que traiga.
                    color: venta.esContado ? null : colores.outline,
                  ),
                ),
              ),
        ],
      ),
    );
  }
}

class _Renglon extends StatelessWidget {
  const _Renglon({
    required this.etiqueta,
    required this.monto,
    required this.nota,
    this.apagado = false,
    this.negrita = false,
  });

  final String etiqueta;
  final Dinero monto;
  final String nota;
  final bool apagado;
  final bool negrita;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final color = apagado ? colores.outline : null;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  etiqueta,
                  style: TextStyle(
                    fontSize: 15,
                    color: color,
                    fontWeight: negrita ? FontWeight.bold : null,
                  ),
                ),
                Text(nota, style: TextStyle(fontSize: 12, color: colores.outline)),
              ],
            ),
          ),
          Text(
            '\$${monto.texto}',
            style: TextStyle(
              fontSize: 17,
              color: color,
              fontWeight: negrita ? FontWeight.bold : FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}
