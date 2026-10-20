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
///
/// Cada venta se puede **reimprimir** desde aquí (ADR 0002 §159): el cliente
/// perdió su ticket, o la impresora no tenía papel al cobrar. Sale de la base,
/// también sin señal; si ya se había impreso, como «COPIA n».
///
/// Y **cancelar** (ADR 0002 §161), por si el vendedor se equivocó: con su motivo,
/// sin señal, y la mercancía vuelve al camión ahí mismo. O **modificarla** (§162):
/// subir, bajar o quitar artículos, con el camión ajustado, e imprimir el ticket
/// nuevo. Después del corte de ese día, ni una ni otra: la cambia la oficina.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/impresora.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../estado/alta.dart' show servicioUbicacionProvider;
import '../../estado/carrito.dart';
import '../../estado/impresora.dart';
import '../../estado/mi_dia.dart';
import '../../estado/sesion.dart';
import 'modificar_venta.dart';
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

  /// La venta cuyo ticket se está mandando a la impresora, para no mandarlo dos
  /// veces con dos toques.
  String? _imprimiendo;

  /// Vuelve a imprimir el ticket de [venta] (ADR 0002 §159).
  ///
  /// Lo que sale lo decide `ReimpresionDeVentas`: el original guardado con
  /// «COPIA n», o el original si nunca salió. Se anota DESPUÉS de imprimir con
  /// éxito, igual que al cobrar: marcar antes dejaría copias que nunca salieron.
  Future<void> _reimprimir(VentaDeMiDia venta) async {
    final sesion = ref.read(sesionProvider);
    final vendedor = sesion is SesionAbierta ? sesion.credencial.nombre : 'Vendedor';
    final reimpresion = ref.read(reimpresionDeVentasProvider);
    final ticket = reimpresion.ticketDe(
      venta.id,
      negocio: ref.read(negocioProvider),
      nombreVendedor: vendedor,
      columnas: ref.read(columnasDelPapelProvider),
    );
    if (ticket == null) {
      _avisar('Esta venta ya no se puede imprimir: la oficina la canceló.');
      return;
    }

    setState(() => _imprimiendo = venta.id);
    final resultado = await ref.read(impresoraProvider).imprimir(ticket.bytes);
    if (!mounted) return;
    setState(() => _imprimiendo = null);

    if (resultado is ImpresionHecha) {
      try {
        reimpresion.anotarImpresa(venta.id, ticket);
      } on Object {
        // El papel ya salió; no perder eso por no poder anotar la marca.
      }
    }
    _avisar(switch (resultado) {
      ImpresionHecha() => ticket.esCopia
          ? 'Listo: salió la copia ${ticket.copia} de ${venta.folio}.'
          : 'Listo: salió el ticket de ${venta.folio}.',
      ImpresoraSinPapel() => 'La impresora no tiene papel. Pon papel y vuelve a intentar.',
      ImpresoraDesconectada() => 'La impresora no está conectada. Revisa que esté encendida.',
      ImpresoraNoConfigurada() => 'Este equipo no tiene impresora configurada: elígela en el '
          'menú de «Mi ruta» → «Impresora».',
      ImpresionFallida(:final detalle) => 'No se pudo imprimir: $detalle',
    });
  }

  /// Cancela [venta] con el motivo que escriba el vendedor (ADR 0002 §161).
  Future<void> _cancelar(VentaDeMiDia venta) async {
    final motivo = await showDialog<String>(
      context: context,
      builder: (_) => _DialogoCancelarVenta(venta: venta),
    );
    if (motivo == null || !mounted) return;
    final ubicacion = await _dondeEstoy();
    if (!mounted) return;

    try {
      ref
          .read(cancelacionDeVentaProvider)
          .cancelar(venta.id, motivo: motivo, ubicacion: ubicacion);
    } on CambioRechazado catch (e) {
      _avisar(switch (e.motivo) {
        MotivoNoSeToca.faltaMotivo => 'Escribe por qué la cancelas.',
        MotivoNoSeToca.diaCortado => 'Ya hiciste el corte de ese día: para '
            'cancelarla, pídeselo a la oficina.',
        MotivoNoSeToca.otroDia => 'Es de un día que ya pasó: para cancelarla, '
            'pídeselo a la oficina.',
        MotivoNoSeToca.yaCancelada => 'Esa venta ya estaba cancelada.',
        MotivoNoSeToca.noExiste => 'Esa venta ya no está en el teléfono.',
        MotivoNoSeToca.sinRenglones ||
        MotivoNoSeToca.sinCambios ||
        MotivoNoSeToca.sinExistencia =>
          'No se pudo cancelar.',
      });
      return;
    } on Object catch (e) {
      _avisar('No se pudo cancelar: $e');
      return;
    }

    // La tienda vuelve a la lista con su número (§156), la cola tiene un sobre
    // más y el camión cambió.
    ref.invalidate(clientesProvider);
    ref.invalidate(resumenColaProvider);
    ref.read(revisionDelCamionProvider.notifier).state++;
    _avisar('Venta ${venta.folio} cancelada. La mercancía volvió a tu camión. '
        'Si le dejaste el ticket, recógelo.');
  }

  /// Dónde está el vendedor, para la pista del cambio (§162). Corto, como al
  /// cobrar: sin GPS se guarda igual.
  Future<Ubicacion?> _dondeEstoy() async {
    try {
      final lectura = await ref
          .read(servicioUbicacionProvider)
          .leer(tiempoLimite: const Duration(seconds: 4));
      return lectura is GpsObtenido ? lectura.ubicacion : null;
    } on Object {
      return null;
    }
  }

  /// Modifica [venta] (ADR 0002 §162) y ofrece el ticket nuevo.
  Future<void> _modificar(VentaDeMiDia venta) async {
    final total = await Navigator.of(context).push<Dinero>(
      MaterialPageRoute(builder: (_) => PantallaModificarVenta(venta: venta)),
    );
    if (total == null || !mounted) return;
    ref.invalidate(clientesProvider);
    ref.invalidate(resumenColaProvider);
    ref.read(revisionDelCamionProvider.notifier).state++;
    ScaffoldMessenger.of(context)
      ..removeCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          duration: const Duration(seconds: 8),
          content: Text(
            'Venta ${venta.folio} modificada: total \$${total.texto}. Imprime el '
            'ticket nuevo y recoge el de antes.',
          ),
          action: SnackBarAction(
            key: const Key('imprimir_modificada'),
            label: 'Imprimir',
            onPressed: () => _reimprimir(venta),
          ),
        ),
      );
  }

  void _avisar(String mensaje) => ScaffoldMessenger.of(context)
    ..removeCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(mensaje)));

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
          // Solo efectivo (ADR 0002 §81). Una transferencia de antes del cambio
          // se sigue mostrando aparte, justamente para que no se cuente: un
          // vendedor que la suma al dinero cree que le falta en la caja.
          if (!dia.transferencias.esCero)
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
                            // Las cancela o corrige la oficina, o el vendedor
                            // las cancela (§161).
                            dia.tocadasPorOficina.length == 1
                                ? 'Una venta de $elDia cancelada o corregida'
                                : '${dia.tocadasPorOficina.length} ventas de '
                                    '$elDia canceladas o corregidas',
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
              // Al tocarla se despliega lo que se le vendió —lo que el vendedor
              // necesita para contestar «¿qué me dejaste el martes?»— y el botón
              // para reimprimir su ticket (ADR 0002 §159).
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
                  '${venta.folio} · ${venta.formaDePago.etiqueta.toLowerCase()}'
                  // Le agregó artículos desde aquí (§162).
                  '${venta.modificaciones > 0 ? ' · modificada' : ''}',
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
                  // Por renglón y no por producto: el mismo artículo puede ir en
                  // dos renglones —cajas y piezas, o lo que se agregó (§162)—.
                  for (final (i, p) in venta.partidas.indexed)
                    Padding(
                      key: Key('partida_${venta.folio}_${i + 1}'),
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
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Wrap(
                      spacing: 4,
                      children: [
                        TextButton.icon(
                          key: Key('reimprimir_${venta.folio}'),
                          onPressed:
                              _imprimiendo != null ? null : () => _reimprimir(venta),
                          icon: _imprimiendo == venta.id
                              ? const SizedBox(
                                  height: 16,
                                  width: 16,
                                  child: CircularProgressIndicator(strokeWidth: 2),
                                )
                              : const Icon(Icons.print_outlined, size: 18),
                          label: const Text('Reimprimir el ticket'),
                        ),
                        if (ref.read(cancelacionDeVentaProvider).porQueNo(venta.id) == null)
                          TextButton.icon(
                            key: Key('modificar_${venta.folio}'),
                            onPressed: _imprimiendo != null ? null : () => _modificar(venta),
                            icon: const Icon(Icons.edit_outlined, size: 18),
                            label: const Text('Modificar'),
                          ),
                        if (ref.read(cancelacionDeVentaProvider).porQueNo(venta.id) == null)
                          TextButton.icon(
                            key: Key('cancelar_${venta.folio}'),
                            onPressed: _imprimiendo != null ? null : () => _cancelar(venta),
                            style: TextButton.styleFrom(foregroundColor: colores.error),
                            icon: const Icon(Icons.cancel_outlined, size: 18),
                            label: const Text('Cancelar la venta'),
                          ),
                      ],
                    ),
                  ),
                  // Por qué ya no se cancela desde aquí, si es el caso (§161).
                  if (switch (ref.read(cancelacionDeVentaProvider).porQueNo(venta.id)) {
                    MotivoNoSeToca.diaCortado =>
                      'Ya hiciste el corte de este día: para cancelarla o modificarla, '
                          'pídeselo a la oficina.',
                    MotivoNoSeToca.otroDia =>
                      'Es de un día que ya pasó: para cancelarla o modificarla, '
                          'pídeselo a la oficina.',
                    _ => null,
                  }
                      case final porQue?)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        porQue,
                        key: Key('cancelar_tras_corte_${venta.folio}'),
                        style: TextStyle(fontSize: 12, color: colores.outline),
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

/// El motivo de la cancelación (ADR 0002 §161). Devuelve el texto, o nulo si el
/// vendedor se arrepiente.
///
/// Los motivos de un toque son los errores de todos los días; el texto se puede
/// cambiar y completar. Se pide lo mismo que en el panel: es lo que alguien va a
/// leer dentro de seis meses.
class _DialogoCancelarVenta extends StatefulWidget {
  const _DialogoCancelarVenta({required this.venta});

  final VentaDeMiDia venta;

  @override
  State<_DialogoCancelarVenta> createState() => _EstadoDialogoCancelarVenta();
}

const _motivosDeUnToque = [
  'Me equivoqué de cliente',
  'Me equivoqué de cantidades',
  'Me equivoqué de producto',
  'El cliente ya no la quiso',
];

class _EstadoDialogoCancelarVenta extends State<_DialogoCancelarVenta> {
  final _motivo = TextEditingController();

  @override
  void dispose() {
    _motivo.dispose();
    super.dispose();
  }

  bool get _alcanza =>
      _motivo.text.trim().length >= largoMinimoDelMotivo;

  @override
  Widget build(BuildContext context) {
    final v = widget.venta;
    return AlertDialog(
      key: const Key('dialogo_cancelar_venta'),
      title: Text('¿Cancelar la venta ${v.folio}?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${v.cliente} · \$${v.total.texto}. La mercancía vuelve a tu camión y '
              'la tienda vuelve a la lista. Si ya le diste el ticket, recógelo.',
            ),
            const SizedBox(height: 12),
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
              key: const Key('motivo_cancelar_venta'),
              controller: _motivo,
              maxLines: 2,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                labelText: '¿Por qué la cancelas?',
                border: OutlineInputBorder(),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          key: const Key('no_cancelar_venta'),
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('No'),
        ),
        FilledButton(
          key: const Key('confirmar_cancelar_venta'),
          style: FilledButton.styleFrom(
            backgroundColor: Theme.of(context).colorScheme.error,
          ),
          onPressed: _alcanza ? () => Navigator.of(context).pop(_motivo.text.trim()) : null,
          child: const Text('Cancelar la venta'),
        ),
      ],
    );
  }
}
