/// Piezas que comparten las pantallas de Gerencia.
///
/// La más importante es `MarcaDeFrescura`, y no es un adorno: en un DSD las
/// cifras del día son un **piso**, no un total (§0.3). Un camión sin señal
/// desde las 10 de la mañana tiene ventas reales que no están en ninguna
/// tarjeta. Una cifra sin su antigüedad invita a tomarla por completa.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';

/// "hace 3 min", "hace 2 h", "ayer". En palabras y no en hora exacta.
///
/// La hora exacta obliga a restar mentalmente, y a las 10:41 de la mañana
/// "10:05" no se lee como "hace 36 minutos": se lee como "reciente".
String antiguedadEnPalabras(DateTime? momento, {DateTime? ahora}) {
  if (momento == null) return 'nunca';
  final transcurrido = (ahora ?? DateTime.now()).difference(momento);
  if (transcurrido.isNegative) {
    // El reloj del teléfono atrasado respecto al servidor. Pasa, y decir
    // "hace -3 minutos" sería peor que admitir que no se sabe.
    return 'hace un momento';
  }
  final minutos = transcurrido.inMinutes;
  if (minutos < 1) return 'hace segundos';
  if (minutos == 1) return 'hace 1 min';
  if (minutos < 60) return 'hace $minutos min';
  final horas = transcurrido.inHours;
  if (horas == 1) return 'hace 1 h';
  if (horas < 24) return 'hace $horas h';
  final dias = transcurrido.inDays;
  return dias == 1 ? 'ayer' : 'hace $dias días';
}

/// Importe en pesos, con separador de miles.
///
/// Se formatea desde los centavos enteros, nunca desde un `double`: el punto de
/// que `Dinero` exista es que el importe no pase por coma flotante ni para
/// pintarse.
String pesos(Dinero monto, {bool conCentavos = true}) {
  final negativo = monto.esNegativo;
  final centavos = negativo ? -monto.centavos : monto.centavos;
  final enteros = centavos ~/ 100;
  final resto = (centavos % 100).toString().padLeft(2, '0');

  final digitos = enteros.toString();
  final conMiles = StringBuffer();
  for (var i = 0; i < digitos.length; i++) {
    if (i > 0 && (digitos.length - i) % 3 == 0) conMiles.write(',');
    conMiles.write(digitos[i]);
  }
  final signo = negativo ? '-' : '';
  return conCentavos ? '$signo\$$conMiles.$resto' : '$signo\$$conMiles';
}

/// La línea de antigüedad y advertencia que acompaña a las cifras.
///
/// Las dos cosas juntas son la advertencia: "hace 2 min" suena perfecto, y si
/// en ese minuto dos teléfonos no habían subido su día, el total es un piso.
class MarcaDeFrescura extends StatelessWidget {
  const MarcaDeFrescura({
    super.key,
    required this.frescura,
    required this.recibidoEn,
    required this.deLaCopia,
    this.ahora,
  });

  final Frescura frescura;

  /// Cuándo lo recibió ESTE teléfono. Es otra cosa que `calculadoEn`, y las dos
  /// se muestran: el servidor calculó a las 10:05 y el teléfono lo bajó a las
  /// 10:40, así que la cifra arrastra 35 minutos más de camino.
  final DateTime recibidoEn;
  final bool deLaCopia;
  final DateTime? ahora;

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    final grave = deLaCopia || !frescura.confiable;

    final lineas = <String>[
      if (deLaCopia)
        'Sin conexión. Estas cifras son la última copia que bajó este '
            'teléfono, ${antiguedadEnPalabras(recibidoEn, ahora: ahora)}.'
      else if (frescura.nuncaCalculado)
        'El servidor no ha calculado el tablero todavía.'
      else
        'Calculado en el servidor ${antiguedadEnPalabras(frescura.calculadoEn, ahora: ahora)}'
            ' · bajado ${antiguedadEnPalabras(recibidoEn, ahora: ahora)}.',
      if (frescura.advertencia != null) frescura.advertencia!,
    ];

    return Container(
      key: const Key('marca_de_frescura'),
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: grave
            ? esquema.errorContainer
            : esquema.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            deLaCopia
                ? Icons.cloud_off_outlined
                : grave
                    ? Icons.warning_amber_outlined
                    : Icons.schedule,
            size: 18,
            color: grave ? esquema.onErrorContainer : esquema.onSurfaceVariant,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              lineas.join(' '),
              style: TextStyle(
                fontSize: 13,
                color:
                    grave ? esquema.onErrorContainer : esquema.onSurfaceVariant,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Una tarjeta del tablero: una cifra grande, su etiqueta y su detalle.
class Tarjeta extends StatelessWidget {
  const Tarjeta({
    super.key,
    required this.cifra,
    required this.etiqueta,
    this.detalle,
    this.alerta = false,
    this.onTap,
  });

  final String cifra;
  final String etiqueta;
  final String? detalle;

  /// Sube el contraste. Se reserva para lo que hay que atender hoy: si todo
  /// está en rojo, el rojo deja de significar algo.
  final bool alerta;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    final cuerpo = Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            cifra,
            style: TextStyle(
              fontSize: 26,
              fontWeight: FontWeight.bold,
              color: alerta ? esquema.error : esquema.onSurface,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            etiqueta,
            style: TextStyle(fontSize: 13, color: esquema.onSurfaceVariant),
          ),
          if (detalle != null) ...[
            const SizedBox(height: 6),
            Text(
              detalle!,
              style: TextStyle(fontSize: 12, color: esquema.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );

    return Card(
      margin: EdgeInsets.zero,
      color: alerta ? esquema.errorContainer : null,
      child: onTap == null ? cuerpo : InkWell(onTap: onTap, child: cuerpo),
    );
  }
}

/// Barra de avance con la marca de lo esperado a prorrata.
///
/// La marca es lo que vuelve útil el porcentaje: 67% el día 10 es excelente y
/// el día 28 es un problema, y el número es el mismo.
class BarraDeAvance extends StatelessWidget {
  const BarraDeAvance({
    super.key,
    required this.logrado,
    required this.esperado,
    required this.semaforo,
  });

  final double logrado;
  final double esperado;
  final String semaforo;

  Color _color(ColorScheme esquema) => switch (semaforo) {
        'adelante' => esquema.primary,
        'cerca' => Colors.orange.shade700,
        _ => esquema.error,
      };

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    return LayoutBuilder(
      builder: (_, limites) {
        final ancho = limites.maxWidth;
        return SizedBox(
          height: 10,
          child: Stack(
            children: [
              Container(
                decoration: BoxDecoration(
                  color: esquema.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(5),
                ),
              ),
              Container(
                width: ancho * (logrado.clamp(0, 100) / 100),
                decoration: BoxDecoration(
                  color: _color(esquema),
                  borderRadius: BorderRadius.circular(5),
                ),
              ),
              // La marca de lo esperado: una línea vertical, no un color.
              Positioned(
                left: (ancho * (esperado.clamp(0, 100) / 100)) - 1,
                child: Container(width: 2, height: 10, color: esquema.onSurface),
              ),
            ],
          ),
        );
      },
    );
  }
}
