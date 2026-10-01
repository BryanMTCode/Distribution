/// Registrar un pago del cliente, y entregarle su recibo.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL SALDO QUE SE MUESTRA LLEVA SU ANTIGÜEDAD AL LADO
/// ─────────────────────────────────────────────────────────────────────────
/// El número que el teléfono conoce es una **caché** (§0.3) y puede tener horas:
/// no incluye los cobros que otros equipos hicieron hoy, ni las ventas que la
/// oficina capturó a mano. Mostrarlo sin decir de cuándo es haría que el vendedor
/// lo tratara como la verdad y discutiera con el cliente sobre un número viejo.
///
/// Por eso la pantalla muestra el saldo, **cuándo se sincronizó**, y un botón de
/// "cobrar todo" que usa ese número como sugerencia — nunca como tope.
///
/// ─────────────────────────────────────────────────────────────────────────
/// NO HAY TOPE: SE PUEDE COBRAR MÁS DE LO QUE DICE QUE DEBE
/// ─────────────────────────────────────────────────────────────────────────
/// El cliente puede liquidar y dejar anticipo, o ya haber pagado parte por otra
/// vía. **El dinero está sobre el mostrador.** Si la pantalla lo impidiera, el
/// vendedor se guardaría efectivo sin documento, que es exactamente el descuadre
/// que esta pantalla existe para evitar. El servidor lo registra como saldo a
/// favor y lo marca para que la oficina decida.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA IMPRESIÓN ES A UN TOQUE, DESPUÉS DE GUARDAR
/// ─────────────────────────────────────────────────────────────────────────
/// Igual que la venta (ADR 0002 §8). Un cobro en efectivo sin papel es la palabra
/// del vendedor contra la del cliente, así que el recibo importa todavía más — y
/// por eso el reintento tiene que estar en la misma pantalla, no en otra.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/impresora.dart';
import '../../datos/repo_clientes.dart';
import '../../estado/abonos.dart';
import '../../estado/carrito.dart';
import '../../estado/sesion.dart';
import 'comunes.dart';
import 'vista_ticket.dart';

class PantallaAbono extends ConsumerStatefulWidget {
  const PantallaAbono({super.key, required this.cliente});

  final ClienteEnRuta cliente;

  @override
  ConsumerState<PantallaAbono> createState() => _EstadoAbono();
}

class _EstadoAbono extends ConsumerState<PantallaAbono> {
  final _importe = TextEditingController();
  final _referencia = TextEditingController();
  FormaDePago _forma = FormaDePago.efectivo;
  String? _errorDeImporte;

  @override
  void dispose() {
    _importe.dispose();
    _referencia.dispose();
    super.dispose();
  }

  /// Lee el importe tecleado.
  ///
  /// Pasa por `Dinero.deTexto`, que exige dos decimales exactos, así que el texto
  /// se normaliza antes: quien teclea "500" quiere decir 500.00, y quien teclea
  /// "1,250.5" quiere decir 1250.50. Ningún `double` toca el número.
  Dinero? _leerImporte() {
    final crudo = _importe.text.trim().replaceAll(',', '').replaceAll(r'$', '');
    if (crudo.isEmpty) {
      setState(() => _errorDeImporte = 'Escribe cuánto está pagando.');
      return null;
    }
    final partes = crudo.split('.');
    if (partes.length > 2) {
      setState(() => _errorDeImporte = 'Eso no es una cantidad.');
      return null;
    }
    final enteros = partes.first.isEmpty ? '0' : partes.first;
    final decimales = (partes.length == 2 ? partes[1] : '').padRight(2, '0');
    if (decimales.length > 2 ||
        !RegExp(r'^\d+$').hasMatch(enteros) ||
        !RegExp(r'^\d*$').hasMatch(decimales)) {
      setState(() => _errorDeImporte = 'Eso no es una cantidad.');
      return null;
    }
    try {
      final dinero = Dinero.deTexto('$enteros.$decimales');
      if (dinero.centavos <= 0) {
        setState(() => _errorDeImporte = 'El importe tiene que ser mayor que cero.');
        return null;
      }
      setState(() => _errorDeImporte = null);
      return dinero;
    } on FormatException {
      setState(() => _errorDeImporte = 'Eso no es una cantidad.');
      return null;
    }
  }

  void _registrar() {
    final importe = _leerImporte();
    if (importe == null) return;

    ref.read(abonoProvider.notifier).registrar(
          clienteId: widget.cliente.id,
          importe: importe,
          formaDePago: _forma,
          referencia: _referencia.text,
          // Forense: lo que este teléfono creía que debía.
          saldoAntes: widget.cliente.credito.saldoEfectivo,
        );
  }

  @override
  Widget build(BuildContext context) {
    final estado = ref.watch(abonoProvider);
    final folios = ref.watch(foliosDeCobroProvider);

    if (estado is AbonoRegistrado) {
      return _Recibo(
        cobro: estado.cobro,
        cliente: widget.cliente,
        alTerminar: () {
          ref.read(abonoProvider.notifier).reiniciar();
          Navigator.of(context).pop();
        },
      );
    }

    final saldo = widget.cliente.credito.saldoEfectivo;

    return Scaffold(
      appBar: AppBar(title: const Text('Registrar pago')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            widget.cliente.nombreComercial,
            style: Theme.of(context).textTheme.titleLarge,
          ),
          if (widget.cliente.codigo != null)
            Text(widget.cliente.codigo!,
                style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 16),

          // El saldo CON su antigüedad. Un número sin fecha se trata como la
          // verdad, y éste no lo es.
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Debe (según la última sincronización)'),
                  Text(
                    '\$${saldo.texto}',
                    style: Theme.of(context).textTheme.headlineMedium,
                  ),
                  Text(
                    antiguedadDelSaldo(widget.cliente.saldoCacheEn),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          TextField(
            key: const Key('campo_importe'),
            controller: _importe,
            autofocus: true,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]')),
            ],
            decoration: InputDecoration(
              labelText: 'Cuánto está pagando',
              prefixText: '\$ ',
              errorText: _errorDeImporte,
              border: const OutlineInputBorder(),
            ),
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          if (saldo.centavos > 0)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: () => setState(() {
                  _importe.text = saldo.texto;
                  _errorDeImporte = null;
                }),
                child: Text('Cobrar todo (\$${saldo.texto})'),
              ),
            ),
          const SizedBox(height: 16),

          const Text('Cómo paga'),
          const SizedBox(height: 6),
          SegmentedButton<FormaDePago>(
            segments: [
              for (final f in FormaDePago.values)
                ButtonSegment(value: f, label: Text(f.etiqueta)),
            ],
            selected: {_forma},
            onSelectionChanged: (s) => setState(() => _forma = s.first),
          ),
          if (!_forma.entraAlArqueo) ...[
            const SizedBox(height: 16),
            TextField(
              key: const Key('campo_referencia'),
              controller: _referencia,
              decoration: const InputDecoration(
                labelText: 'Referencia',
                helperText:
                    'Sin ella la oficina no puede encontrar el pago en el banco.',
                border: OutlineInputBorder(),
              ),
            ),
          ],

          if (folios != null && folios.porAgotarse) ...[
            const SizedBox(height: 16),
            Aviso(
              'Te quedan ${folios.restantes} folios de cobro. Sincroniza cuando '
              'tengas señal para pedir más.',
            ),
          ],

          if (estado is AbonoFallido) ...[
            const SizedBox(height: 16),
            Aviso(estado.mensaje, grave: true),
          ],
          if (estado is AbonoSinIdentidad) ...[
            const SizedBox(height: 16),
            const Aviso(
              'Este equipo no tiene credencial o no está registrado. Vuelve a '
              'entrar con señal.',
              grave: true,
            ),
          ],

          const SizedBox(height: 24),
          FilledButton(
            key: const Key('registrar_pago'),
            onPressed: estado is AbonoEnCurso ? null : _registrar,
            child: estado is AbonoEnCurso
                ? const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Registrar el pago'),
          ),
          const SizedBox(height: 8),
          Text(
            'Se puede cobrar más de lo que dice que debe: el saldo de arriba '
            'puede tener horas. La oficina lo registra como saldo a favor.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// El recibo recién hecho, con su botón de imprimir.
class _Recibo extends ConsumerStatefulWidget {
  const _Recibo({
    required this.cobro,
    required this.cliente,
    required this.alTerminar,
  });

  final CobroGuardado cobro;
  final ClienteEnRuta cliente;
  final VoidCallback alTerminar;

  @override
  ConsumerState<_Recibo> createState() => _EstadoRecibo();
}

class _EstadoRecibo extends ConsumerState<_Recibo> {
  bool _imprimiendo = false;
  int _impresiones = 0;
  String? _error;
  String? _dondeQuedo;

  /// Los bytes del original, congelados: la reimpresión los reusa con el aviso de
  /// copia encima, para que el papel diga exactamente lo mismo.
  List<int>? _original;

  List<int> _armar() {
    final sesion = ref.read(sesionProvider);
    final vendedor =
        sesion is SesionAbierta ? sesion.credencial.nombre : 'Vendedor';
    return ticketDeCobro(
      widget.cobro,
      negocio: ref.read(negocioProvider),
      visita: DatosDeLaVisita(
        nombreCliente: widget.cliente.nombreComercial,
        nombreVendedor: vendedor,
        codigoCliente: widget.cliente.codigo,
      ),
    );
  }

  Future<void> _imprimir() async {
    setState(() {
      _imprimiendo = true;
      _error = null;
    });

    _original ??= _armar();
    final esCopia = _impresiones > 0;
    final aImprimir = esCopia
        ? reimpresionDeCobro(_original!, _impresiones)
        : _original!;

    final resultado = await ref.read(impresoraProvider).imprimir(aImprimir);
    if (!mounted) return;

    // Cada falla pide algo distinto del vendedor, y ninguna debe parecerse a las
    // otras: "sin papel" se resuelve poniendo papel, "desconectada" emparejando.
    final mensaje = switch (resultado) {
      ImpresionHecha() => null,
      ImpresoraSinPapel() =>
        'La impresora no tiene papel. Pon papel y vuelve a intentar.',
      ImpresoraDesconectada() =>
        'La impresora no está conectada. Revisa que esté encendida.',
      ImpresoraNoConfigurada() => 'Este equipo no tiene impresora configurada.',
      ImpresionFallida(:final detalle) => 'No se pudo imprimir: $detalle',
    };

    if (resultado is ImpresionHecha) {
      // La marca va DESPUÉS de que la impresora confirmó: marcarla antes dejaría
      // el cobro como impreso cuando el papel nunca salió, y la oficina no podría
      // distinguir un recibo perdido de uno que nunca se imprimió.
      try {
        ref
            .read(registroDeCobroProvider)
            ?.marcarImpreso(widget.cobro.id, ticket: _original!);
      } on Object {
        // El cobro ya está guardado y el papel ya salió. No perder eso por no
        // poder anotar la marca.
      }
    }

    setState(() {
      _imprimiendo = false;
      _error = mensaje;
      if (resultado is ImpresionHecha) {
        _impresiones++;
        _dondeQuedo = resultado.donde;
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final cobro = widget.cobro;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Pago registrado'),
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
                // El folio, grande: es lo que el cliente anota y lo que la oficina
                // pide cuando algo se aclara por teléfono.
                Text(
                  cobro.folioLocal,
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
                Text(
                  '\$${cobro.importe.texto} · ${cobro.formaDePago.etiqueta}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                if (cobro.referencia != null)
                  Text('Ref. ${cobro.referencia}',
                      style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
          const SizedBox(height: 24),

          if (_error != null) Aviso(_error!, grave: true),
          if (_dondeQuedo != null)
            Aviso('Recibo guardado en $_dondeQuedo'),

          const SizedBox(height: 8),
          FilledButton.icon(
            key: const Key('imprimir_recibo'),
            onPressed: _imprimiendo ? null : _imprimir,
            icon: _imprimiendo
                ? const SizedBox(
                    height: 18,
                    width: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.print),
            label: Text(
              _impresiones == 0 ? 'Imprimir recibo' : 'Imprimir otra copia',
            ),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => PantallaVistaTicket(
                  bytes: _original ??= _armar(),
                  dondeQuedo: _dondeQuedo,
                ),
              ),
            ),
            icon: const Icon(Icons.receipt_long),
            label: const Text('Ver el recibo en pantalla'),
          ),
          const SizedBox(height: 24),
          TextButton(
            onPressed: widget.alTerminar,
            child: const Text('Listo'),
          ),
          const SizedBox(height: 8),
          Text(
            'El pago ya está en la cola: llega a la oficina en la siguiente '
            'sincronización. Mientras, este recibo es la prueba del cliente.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// De cuándo es el saldo que se está mostrando.
///
/// Se muestra SIEMPRE, incluso cuando es de hace un minuto. Un número sin fecha se
/// trata como la verdad, y éste es una caché: no incluye los cobros que otros
/// equipos hicieron hoy ni lo que la oficina capturó a mano (§0.3).
String antiguedadDelSaldo(String? sincronizadoEn, {DateTime? ahora}) {
  if (sincronizadoEn == null) return 'Nunca se ha sincronizado.';
  final momento = DateTime.tryParse(sincronizadoEn);
  if (momento == null) return 'Nunca se ha sincronizado.';

  final transcurrido = (ahora ?? DateTime.now()).difference(momento.toLocal());
  if (transcurrido.isNegative || transcurrido.inMinutes < 2) {
    return 'Actualizado ahora.';
  }
  if (transcurrido.inHours < 1) {
    return 'Actualizado hace ${transcurrido.inMinutes} min.';
  }
  if (transcurrido.inHours < 24) {
    final horas = transcurrido.inHours;
    return 'Actualizado hace $horas h. Puede haber cambiado.';
  }
  final dias = transcurrido.inDays;
  return 'Actualizado hace $dias día${dias == 1 ? '' : 's'}. '
      'Sincroniza antes de discutir el saldo con el cliente.';
}
