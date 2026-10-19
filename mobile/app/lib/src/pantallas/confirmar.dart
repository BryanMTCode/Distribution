/// Antes de quitar algo, una segunda confirmación (ADR 0002 §143).
///
/// «Para eliminar algo, como la impresora, quiero que aparezca una doble
/// confirmación.» Un toque de más en un teléfono, con el cliente enfrente, no
/// debe borrar un renglón ni dejar el camión sin impresora.
library;

import 'package:flutter/material.dart';

/// `true` si se confirmó. El botón que confirma dice la acción («Quitar»,
/// «Olvidar»), no «Aceptar»: que se lea qué va a pasar.
Future<bool> confirmarAntesDeQuitar(
  BuildContext context, {
  required String titulo,
  String? detalle,
  String accion = 'Quitar',
}) async {
  final si = await showDialog<bool>(
    context: context,
    builder: (contexto) => AlertDialog(
      key: const Key('dialogo_confirmar_quitar'),
      title: Text(titulo),
      content: detalle == null ? null : Text(detalle),
      actions: [
        TextButton(
          key: const Key('confirmar_quitar_no'),
          onPressed: () => Navigator.of(contexto).pop(false),
          child: const Text('No'),
        ),
        FilledButton(
          key: const Key('confirmar_quitar_si'),
          onPressed: () => Navigator.of(contexto).pop(true),
          child: Text(accion),
        ),
      ],
    ),
  );
  return si ?? false;
}
