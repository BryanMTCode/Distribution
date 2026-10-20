/// Un ticket del cierre del día, para leerlo, compartirlo e imprimirlo
/// (ADR 0002 §82 y §133).
///
/// Lo usan el vendedor (su corte y su solicitud de carga) y el gerente (la carga
/// que acaba de aceptar). El texto sale de `dsd_core` —el mismo para los dos—
/// y se comparte como texto: WhatsApp lo pinta con sus negritas, y cualquier
/// otra aplicación lo recibe igual de legible. Los del vendedor —el corte y la
/// solicitud de carga— también se imprimen en la impresora del camión, con ese
/// mismo texto.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/impresora.dart';
import '../estado/carrito.dart';
import '../estado/impresora.dart';
import '../estado/cierre.dart';

class PantallaTicketCompartible extends ConsumerWidget {
  const PantallaTicketCompartible({
    super.key,
    required this.titulo,
    required this.texto,
    this.aviso,
    this.siguiente,
    this.etiquetaSiguiente,
    this.iconoSiguiente = Icons.arrow_forward,
    this.imprimible = false,
  });

  final String titulo;
  final String texto;

  /// Una línea arriba del ticket: «Se manda a la oficina al sincronizar».
  final String? aviso;

  /// El paso que sigue, si hay: después del corte, pedir la carga.
  final VoidCallback? siguiente;
  final String? etiquetaSiguiente;
  final IconData iconoSiguiente;

  /// Si ofrece imprimirlo en la impresora del camión: el corte del vendedor y
  /// su solicitud de carga (ADR 0002 §150).
  final bool imprimible;

  Future<void> _imprimir(BuildContext context, WidgetRef ref) async {
    final resultado = await ref
        .read(impresoraProvider)
        .imprimir(escPosDelTicket(texto, columnas: ref.read(columnasDelPapelProvider)));
    if (!context.mounted) return;
    final mensaje = switch (resultado) {
      ImpresionHecha() => 'Listo: el ticket salió en la impresora.',
      ImpresoraSinPapel() => 'La impresora no tiene papel. Pon papel y vuelve a intentar.',
      ImpresoraDesconectada() => 'La impresora no está conectada. Revisa que esté encendida.',
      ImpresoraNoConfigurada() => 'Este equipo no tiene impresora configurada: elígela en el '
          'menú de «Mi ruta» → «Impresora».',
      ImpresionFallida(:final detalle) => 'No se pudo imprimir: $detalle',
    };
    // El aviso anterior se quita de golpe, sin animarlo: imprimir el corte y luego
    // la solicitud deja ver el segundo aviso enseguida.
    ScaffoldMessenger.of(context)
      ..removeCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(mensaje)));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    return Scaffold(
      key: const Key('pantalla_ticket_compartible'),
      appBar: AppBar(
        title: Text(titulo),
        actions: [
          IconButton(
            key: const Key('boton_copiar_ticket'),
            tooltip: 'Copiar',
            icon: const Icon(Icons.copy_outlined),
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: texto));
              if (!context.mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Copiado. Pégalo donde quieras.')),
              );
            },
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (aviso != null) ...[
            Text(aviso!, key: const Key('aviso_ticket')),
            const SizedBox(height: 12),
          ],
          Card(
            color: colores.surfaceContainerHighest,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: SelectableText(
                texto,
                key: const Key('texto_ticket'),
                style: const TextStyle(fontFamily: 'monospace', fontSize: 13, height: 1.35),
              ),
            ),
          ),
          const SizedBox(height: 16),
          if (imprimible) ...[
            FilledButton.icon(
              key: const Key('boton_imprimir_ticket'),
              onPressed: () => _imprimir(context, ref),
              icon: const Icon(Icons.print_outlined),
              label: const Text('Imprimir'),
            ),
            const SizedBox(height: 8),
          ],
          FilledButton.tonalIcon(
            key: const Key('boton_compartir_ticket'),
            onPressed: () => ref.read(compartidorProvider).compartir(texto, asunto: titulo),
            icon: const Icon(Icons.share_outlined),
            label: const Text('Enviar por WhatsApp (o correo…)'),
          ),
          if (siguiente != null) ...[
            const SizedBox(height: 12),
            OutlinedButton.icon(
              key: const Key('boton_siguiente_ticket'),
              onPressed: siguiente,
              icon: Icon(iconoSiguiente),
              label: Text(etiquetaSiguiente ?? 'Seguir'),
            ),
          ],
        ],
      ),
    );
  }
}
