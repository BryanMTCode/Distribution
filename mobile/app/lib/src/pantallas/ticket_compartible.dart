/// Un ticket del cierre del día, para leerlo y compartirlo (ADR 0002 §82).
///
/// Lo usan el vendedor (su corte y su solicitud de carga) y el gerente (la carga
/// que acaba de aceptar). El texto sale de `dsd_core` —el mismo para los dos—
/// y se comparte como texto: WhatsApp lo pinta con sus negritas, y cualquier
/// otra aplicación lo recibe igual de legible.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
  });

  final String titulo;
  final String texto;

  /// Una línea arriba del ticket: «Se manda a la oficina al sincronizar».
  final String? aviso;

  /// El paso que sigue, si hay: después del corte, pedir la carga.
  final VoidCallback? siguiente;
  final String? etiquetaSiguiente;
  final IconData iconoSiguiente;

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
          FilledButton.icon(
            key: const Key('boton_compartir_ticket'),
            onPressed: () => ref.read(compartidorProvider).compartir(texto, asunto: titulo),
            icon: const Icon(Icons.share_outlined),
            label: const Text('Compartir (WhatsApp, correo…)'),
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
