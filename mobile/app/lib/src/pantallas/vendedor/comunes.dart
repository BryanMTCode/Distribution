/// Piezas de interfaz que comparten las pantallas del vendedor.
library;

import 'package:flutter/material.dart';

/// Un aviso de una línea o dos, en su recuadro.
///
/// Vive aquí y no dentro de una pantalla porque las de merma, no-drop y abono
/// dicen lo mismo de la misma forma —"sincroniza", "esto se te descuenta", "se
/// registra igual"— y tres copias del mismo recuadro se desalinean en cuanto una
/// cambia de color.
///
/// `grave` sube el contraste al par de error. Se usa para lo que el vendedor
/// **tiene** que resolver antes de seguir, nunca para informar: si todo es rojo,
/// el rojo no dice nada.
class Aviso extends StatelessWidget {
  const Aviso(this.texto, {super.key, this.grave = false});

  final String texto;
  final bool grave;

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: grave ? esquema.errorContainer : esquema.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        texto,
        style: TextStyle(
          color: grave ? esquema.onErrorContainer : esquema.onSurfaceVariant,
        ),
      ),
    );
  }
}
