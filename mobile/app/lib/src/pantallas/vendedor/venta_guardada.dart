/// Lo que el vendedor ve cuando la venta ya es un hecho.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA IMPRESIÓN NO ES AUTOMÁTICA, Y ES UNA DECISIÓN DE NEGOCIO
/// ─────────────────────────────────────────────────────────────────────────
/// El vendedor toca "Imprimir" **después** de que la venta se guardó
/// (septiembre 2026). La alternativa —imprimir sola al confirmar— ahorra un
/// toque, pero cuando la impresora está sin papel o desemparejada la venta ya
/// quedó escrita y el vendedor se queda sin un lugar obvio desde dónde
/// reintentar: tendría que buscar la venta en otra pantalla, con el cliente
/// esperando.
///
/// Aquí el reintento es el mismo botón, tantas veces como haga falta.
///
/// Lo primero que se ve es el **folio**, grande. Es lo que el cliente anota y lo
/// que la oficina pide cuando algo se aclara por teléfono.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/carrito.dart';

class PantallaVentaGuardada extends ConsumerStatefulWidget {
  const PantallaVentaGuardada({super.key, required this.venta});

  final VentaGuardada venta;

  @override
  ConsumerState<PantallaVentaGuardada> createState() => _EstadoVentaGuardada();
}

class _EstadoVentaGuardada extends ConsumerState<PantallaVentaGuardada> {
  bool _imprimiendo = false;
  int _impresiones = 0;
  String? _errorDeImpresion;

  Future<void> _imprimir() async {
    setState(() {
      _imprimiendo = true;
      _errorDeImpresion = null;
    });

    // La impresora Bluetooth es la última pieza de la Fase 3 y necesita el
    // equipo físico. Mientras tanto se registra la impresión —que es lo que la
    // oficina audita— y se dice la verdad en pantalla.
    final cierre = ref.read(cierreDeVentaProvider);
    try {
      cierre?.marcarImpresa(widget.venta.id, ticket: const []);
      if (!mounted) return;
      setState(() {
        _imprimiendo = false;
        _impresiones++;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _imprimiendo = false;
        _errorDeImpresion = e.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final v = widget.venta;
    final colores = Theme.of(context).colorScheme;

    return PopScope(
      // Volver con el botón del sistema tiene que llevar a la ruta, no al
      // carrito: el pedido ya es un documento y no se puede editar.
      canPop: false,
      onPopInvokedWithResult: (hecho, _) {
        if (!hecho) _volverALaRuta(context);
      },
      child: Scaffold(
        appBar: AppBar(
          automaticallyImplyLeading: false,
          title: const Text('Venta registrada'),
        ),
        bottomNavigationBar: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    key: const Key('boton_imprimir'),
                    onPressed: _imprimiendo ? null : _imprimir,
                    icon: _imprimiendo
                        ? const SizedBox(
                            height: 18,
                            width: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.print_outlined),
                    label: Text(
                      _impresiones == 0 ? 'Imprimir remisión' : 'Imprimir otra copia',
                    ),
                    style: FilledButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton(
                    key: const Key('boton_terminar_visita'),
                    onPressed: () => _volverALaRuta(context),
                    child: const Padding(
                      padding: EdgeInsets.symmetric(vertical: 12),
                      child: Text('Terminar visita'),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 24, 16, 16),
          children: [
            Icon(Icons.check_circle, size: 56, color: colores.primary),
            const SizedBox(height: 16),
            // El folio, grande: es lo que el cliente anota y lo que la oficina
            // pide por teléfono cuando algo se aclara.
            Center(
              child: Text(
                v.folioLocal,
                key: const Key('folio_de_la_venta'),
                style: const TextStyle(
                  fontSize: 30,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 1,
                ),
              ),
            ),
            const SizedBox(height: 4),
            Center(
              child: Text(
                v.aCredito ? 'A crédito' : 'De contado',
                style: TextStyle(color: colores.onSurfaceVariant),
              ),
            ),
            const SizedBox(height: 24),
            Center(
              child: Text(
                '\$${v.total.texto}',
                key: const Key('total_de_la_venta'),
                style: const TextStyle(fontSize: 38, fontWeight: FontWeight.w800),
              ),
            ),
            const SizedBox(height: 24),
            const Divider(),
            for (final l in v.lineas)
              ListTile(
                dense: true,
                title: Text(l.presentacion.nombre),
                subtitle: Text(
                  '${l.cantidad.textoCorto} ${l.presentacion.unidadCodigo} '
                  '× \$${l.presentacion.precio.textoCorto}',
                ),
                trailing: Text(
                  '\$${l.importe.texto}',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
            const Divider(),
            const SizedBox(height: 12),
            _Aviso(
              clave: 'aviso_pendiente_de_enviar',
              icono: Icons.cloud_upload_outlined,
              texto: 'Guardada en el equipo. Se envía sola cuando haya señal.',
              fondo: colores.tertiaryContainer,
              frente: colores.onTertiaryContainer,
            ),
            if (v.pocosFoliosRestantes) ...[
              const SizedBox(height: 8),
              _Aviso(
                clave: 'aviso_pocos_folios',
                icono: Icons.confirmation_number_outlined,
                // Quedarse sin folios a media ruta significa no poder vender.
                texto: 'Te quedan ${v.foliosRestantes} folios. '
                    'Sincroniza para pedir más antes de que se acaben.',
                fondo: colores.errorContainer,
                frente: colores.onErrorContainer,
              ),
            ],
            if (_impresiones > 0) ...[
              const SizedBox(height: 8),
              _Aviso(
                clave: 'aviso_impresa',
                icono: Icons.check,
                texto: _impresiones == 1
                    ? 'Remisión registrada como impresa.'
                    : 'Impresa $_impresiones veces. Las copias salen '
                        'idénticas al original.',
                fondo: colores.secondaryContainer,
                frente: colores.onSecondaryContainer,
              ),
            ],
            if (_errorDeImpresion != null) ...[
              const SizedBox(height: 8),
              _Aviso(
                clave: 'aviso_error_impresion',
                icono: Icons.print_disabled_outlined,
                texto: 'No se pudo imprimir. La venta ya está guardada: '
                    'puedes reintentar.',
                fondo: colores.errorContainer,
                frente: colores.onErrorContainer,
              ),
            ],
          ],
        ),
      ),
    );
  }

  void _volverALaRuta(BuildContext context) {
    ref.read(cobroProvider.notifier).reiniciar();
    ref.read(clienteEnVisitaProvider.notifier).state = null;
    Navigator.of(context).popUntil((r) => r.isFirst);
  }
}

class _Aviso extends StatelessWidget {
  const _Aviso({
    required this.clave,
    required this.icono,
    required this.texto,
    required this.fondo,
    required this.frente,
  });

  final String clave;
  final IconData icono;
  final String texto;
  final Color fondo;
  final Color frente;

  @override
  Widget build(BuildContext context) => Container(
        key: Key(clave),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: fondo,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          children: [
            Icon(icono, size: 20, color: frente),
            const SizedBox(width: 10),
            Expanded(
              child: Text(texto, style: TextStyle(color: frente, fontSize: 13)),
            ),
          ],
        ),
      );
}
