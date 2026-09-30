/// El ticket en pantalla, como saldría del papel.
///
/// ─────────────────────────────────────────────────────────────────────────
/// PARA QUÉ EXISTE ESTA PANTALLA
/// ─────────────────────────────────────────────────────────────────────────
/// La impresora está en otro Estado. Sin esto, la única forma de revisar el
/// diseño del ticket sería leer un archivo con `adb` desde una computadora, y el
/// diseño se revisa mejor donde se va a usar: en el teléfono, con el pulgar.
///
/// Muestra el ticket decodificado con las mismas reglas que aplica la impresora
/// —alineación, negritas, tamaño doble— en monoespaciado, con el ancho real del
/// papel marcado. Una línea que se desborde se ve inmediatamente.
///
/// Cuando llegue la impresora esta pantalla **sigue sirviendo**: es la forma de
/// comparar lo que se esperaba con lo que salió, cuando algo no cuadre.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class PantallaVistaTicket extends StatelessWidget {
  const PantallaVistaTicket({
    super.key,
    required this.bytes,
    this.dondeQuedo,
  });

  final List<int> bytes;

  /// Ruta del archivo que dejó la impresora simulada, si hay.
  final String? dondeQuedo;

  @override
  Widget build(BuildContext context) {
    final vista = decodificar(bytes);
    final colores = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Ticket'),
        actions: [
          IconButton(
            key: const Key('boton_copiar_ticket'),
            tooltip: 'Copiar el texto',
            icon: const Icon(Icons.copy_all_outlined),
            onPressed: () async {
              // Copiar sirve para pegarlo en un mensaje y comentarlo sin tener
              // que sacar el teléfono del bolsillo de otra persona.
              await Clipboard.setData(ClipboardData(text: vista.renderConMarco()));
              if (!context.mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Ticket copiado')),
              );
            },
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(12),
        children: [
          _Ficha(
            titulo: '${bytes.length} bytes · ${vista.lineas.length} líneas',
            detalle: 'Papel de ${vista.columnas} columnas · '
                'tabla ${vista.tabla?.nombre ?? "sin fijar"}',
            color: colores.secondaryContainer,
            frente: colores.onSecondaryContainer,
          ),
          if (vista.tabla == null) ...[
            const SizedBox(height: 8),
            _Ficha(
              clave: 'aviso_sin_tabla',
              titulo: 'El ticket no fija la tabla de códigos',
              // Sin ESC t, la impresora usa la que trae de fábrica y los acentos
              // salen como le toque. Es un defecto, no un detalle.
              detalle: 'Los acentos saldrían como le toque a la impresora.',
              color: colores.errorContainer,
              frente: colores.onErrorContainer,
            ),
          ],
          if (vista.desconocidos.isNotEmpty) ...[
            const SizedBox(height: 8),
            _Ficha(
              clave: 'aviso_comandos_desconocidos',
              titulo: 'Comandos que la vista previa no entiende',
              detalle: vista.desconocidos.join(', '),
              color: colores.errorContainer,
              frente: colores.onErrorContainer,
            ),
          ],
          const SizedBox(height: 12),
          // Monoespaciado y desplazable en horizontal: si una línea se desborda,
          // se tiene que poder ver que se desborda, no recortarla en silencio.
          Container(
            key: const Key('papel_del_ticket'),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: colores.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: colores.outlineVariant),
            ),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Text(
                vista.renderConMarco(),
                style: const TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 11,
                  height: 1.35,
                ),
              ),
            ),
          ),
          if (dondeQuedo != null) ...[
            const SizedBox(height: 12),
            Text(
              'Guardado en:\n$dondeQuedo',
              key: const Key('ruta_del_archivo'),
              style: TextStyle(fontSize: 11, color: colores.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
  }
}

class _Ficha extends StatelessWidget {
  const _Ficha({
    required this.titulo,
    required this.detalle,
    required this.color,
    required this.frente,
    this.clave,
  });

  final String titulo;
  final String detalle;
  final Color color;
  final Color frente;
  final String? clave;

  @override
  Widget build(BuildContext context) => Container(
        key: clave == null ? null : Key(clave!),
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              titulo,
              style: TextStyle(color: frente, fontWeight: FontWeight.w700, fontSize: 13),
            ),
            Text(detalle, style: TextStyle(color: frente, fontSize: 11)),
          ],
        ),
      );
}
