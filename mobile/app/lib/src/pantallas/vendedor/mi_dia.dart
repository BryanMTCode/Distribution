/// «Mi día»: lo que vendí y el dinero que debo entregar.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTA PANTALLA EXISTE
/// ───────────────────────────────────────────────────────────────────────────
/// Hasta ahora el vendedor llegaba a la bodega sin saber cuánto debía entregar:
/// lo sabía la oficina, al abrir la liquidación. Eso convierte el arqueo en una
/// sorpresa, y una sorpresa con el dinero en la mano se discute.
///
/// El número grande es **la misma cuenta que el arqueo**: las ventas pagadas en
/// efectivo. Si fuera otra, esta pantalla sería una promesa que la oficina no va
/// a cumplir.
///
/// Lee de SQLite, así que funciona sin señal — que es cuando hace falta: a media
/// ruta, decidiendo si alcanza el cambio, y en el patio antes de entrar a cuadrar.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/mi_dia.dart';
import 'cierre_del_dia.dart';

class PantallaMiDia extends ConsumerStatefulWidget {
  const PantallaMiDia({super.key});

  @override
  ConsumerState<PantallaMiDia> createState() => _EstadoMiDia();
}

/// Cuántos días atrás se puede revisar. Los documentos siguen en el teléfono;
/// el límite es para que el calendario no ofrezca meses vacíos.
const _diasHaciaAtras = 90;

class _EstadoMiDia extends ConsumerState<PantallaMiDia> {
  /// El día que se está revisando. Nulo es hoy. Vive en la pantalla: al salir y
  /// volver a entrar, «Mi día» abre otra vez en hoy, que es lo que se espera.
  String? _elegido;

  String _mover(String dia, int dias) =>
      diaOperativoDe(DateTime.parse(dia).add(Duration(days: dias, hours: 12)));

  void _ir(String dia, String hoy) =>
      setState(() => _elegido = dia.compareTo(hoy) >= 0 ? null : dia);

  Future<void> _elegirEnCalendario(String diaActual, String hoy) async {
    final hoyFecha = DateTime.parse(hoy);
    final elegido = await showDatePicker(
      context: context,
      initialDate: DateTime.parse(diaActual),
      firstDate: hoyFecha.subtract(const Duration(days: _diasHaciaAtras)),
      // Un día futuro no tiene ventas: no se ofrece.
      lastDate: hoyFecha,
      helpText: '¿Qué día quieres revisar?',
    );
    if (elegido == null) return;
    _ir(diaOperativoDe(elegido), hoy);
  }

  @override
  Widget build(BuildContext context) {
    final hoy = ref.watch(diaOperativoProvider);
    final diaOperativo = _elegido ?? hoy;
    final esHoy = diaOperativo == hoy;
    final dia = ref.watch(miDiaDelProvider(diaOperativo));
    final colores = Theme.of(context).colorScheme;
    final elDia = esHoy ? 'hoy' : 'ese día';

    return Scaffold(
      // El día de los datos, a la vista: «Mi día» a secas no deja ver si el
      // teléfono ya cambió de día o si lo que se ve es de ayer.
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Mi día'),
            Text(
              encabezadoDelDia(diaOperativo, hoy: hoy),
              key: const Key('dia_de_mi_dia'),
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: Theme.of(context).appBarTheme.foregroundColor,
                  ),
            ),
          ],
        ),
        // Moverse de día: uno atrás, uno adelante (hasta hoy), o el calendario.
        actions: [
          IconButton(
            key: const Key('boton_dia_anterior'),
            tooltip: 'Día anterior',
            icon: const Icon(Icons.chevron_left),
            onPressed: () => _ir(_mover(diaOperativo, -1), hoy),
          ),
          IconButton(
            key: const Key('boton_dia_siguiente'),
            tooltip: 'Día siguiente',
            icon: const Icon(Icons.chevron_right),
            onPressed: esHoy ? null : () => _ir(_mover(diaOperativo, 1), hoy),
          ),
          IconButton(
            key: const Key('boton_calendario_mi_dia'),
            tooltip: 'Elegir el día',
            icon: const Icon(Icons.calendar_month_outlined),
            onPressed: () => _elegirEnCalendario(diaOperativo, hoy),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Revisando otro día: que no se confunda con el corte de hoy.
          if (!esHoy) ...[
            Card(
              key: const Key('aviso_otro_dia'),
              color: colores.tertiaryContainer,
              child: ListTile(
                leading: Icon(Icons.history, color: colores.onTertiaryContainer),
                title: Text(
                  'Estás revisando el ${diaEnPalabras(diaOperativo)}',
                  style: TextStyle(color: colores.onTertiaryContainer),
                ),
                subtitle: Text(
                  'No es el corte de hoy.',
                  style: TextStyle(color: colores.onTertiaryContainer),
                ),
                trailing: TextButton(
                  key: const Key('boton_volver_a_hoy_mi_dia'),
                  onPressed: () => setState(() => _elegido = null),
                  child: const Text('Ver hoy'),
                ),
              ),
            ),
            const SizedBox(height: 8),
          ],
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
                    esHoy ? 'Efectivo que debes entregar' : 'Efectivo de ese día',
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
            etiqueta: 'Ventas en efectivo',
            monto: dia.efectivo,
            nota: 'dinero que trae en la bolsa',
          ),
          // Lo que NO es efectivo, y se muestra justamente para que no se cuente.
          // Un vendedor que suma la transferencia al dinero cree que le falta en
          // la caja.
          _Renglon(
            etiqueta: 'Ventas por transferencia',
            monto: dia.transferencias,
            nota: 'llegó al banco, no a tu bolsa',
            apagado: true,
          ),
          const Divider(height: 24),
          _Renglon(
            etiqueta: 'Total vendido $elDia',
            monto: dia.vendido,
            nota: 'todo de contado',
            negrita: true,
          ),

          // ---------------------------------------------------------------
          // El cierre del día (§82): el corte y la carga de mañana. Solo hoy:
          // un día pasado ya no se corta desde aquí.
          // ---------------------------------------------------------------
          if (esHoy) ...[
            const SizedBox(height: 16),
            const TarjetaDelCierre(),
          ],

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
                                ? 'La oficina cambió una venta de $elDia'
                                : 'La oficina cambió '
                                    '${dia.tocadasPorOficina.length} ventas de $elDia',
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
          Text('Mis ventas de $elDia', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),

          if (dia.ventas.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 24),
              child: Text(
                esHoy ? 'Todavía no has vendido hoy.' : 'Ese día no hubo ventas en este teléfono.',
                textAlign: TextAlign.center,
              ),
            )
          else
            for (final venta in dia.ventas)
              // Al tocarla se despliega lo que se le vendió: lo que el vendedor
              // necesita para contestar «¿qué me dejaste el martes?» sin
              // reimprimir el ticket.
              ExpansionTile(
                key: Key('venta_${venta.folio}'),
                dense: true,
                tilePadding: EdgeInsets.zero,
                childrenPadding: const EdgeInsets.only(left: 36, bottom: 8),
                leading: Icon(
                  venta.sincronizada ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
                  size: 20,
                  color: venta.sincronizada ? colores.outline : colores.error,
                ),
                title: Text(venta.cliente),
                subtitle: Text(
                  '${venta.folio} · ${venta.formaDePago.etiqueta.toLowerCase()}',
                ),
                trailing: Text(
                  '\$${venta.total.texto}',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    // La transferencia se ve distinta en la lista por lo mismo que
                    // en el desglose: no es dinero que traiga.
                    color: venta.formaDePago.entraAlArqueo ? null : colores.outline,
                  ),
                ),
                children: [
                  if (venta.partidas.isEmpty)
                    const Align(
                      alignment: Alignment.centerLeft,
                      child: Text('Sin renglones guardados.'),
                    ),
                  for (final p in venta.partidas)
                    Padding(
                      key: Key('partida_${venta.folio}_${p.producto}'),
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              '${p.cantidadTexto} ${p.unidad} · ${p.producto}',
                            ),
                          ),
                          Text(
                            '\$${p.importe.texto}',
                            style: TextStyle(color: colores.outline),
                          ),
                        ],
                      ),
                    ),
                ],
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
