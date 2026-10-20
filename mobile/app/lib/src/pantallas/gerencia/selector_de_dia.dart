/// ‹ Hoy, jueves 24 de septiembre › y el calendario: ver un día a la vez y
/// moverse de día en día (ADR 0002 §149).
///
/// «Quiero que aparezcan siempre por día y me pueda mover por días.» Lo usan las
/// cargas y los cortes de la app de la oficina, igual que «Mi día» del vendedor.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';

/// El día `dias` antes o después. Al mediodía, para que un cambio de horario no
/// lo deje en el mismo día.
String moverDia(String dia, int dias) =>
    diaOperativoDe(DateTime.parse(dia).add(Duration(days: dias, hours: 12)));

/// El calendario para escoger un día; nulo si se cancela.
Future<String?> elegirDiaEnCalendario(
  BuildContext context, {
  required String dia,
  required String hoy,
  required String ayuda,
  int diasAdelante = 0,
}) async {
  final hoyFecha = DateTime.parse(hoy);
  final elegido = await showDatePicker(
    context: context,
    initialDate: DateTime.parse(dia),
    firstDate: hoyFecha.subtract(const Duration(days: 730)),
    lastDate: hoyFecha.add(Duration(days: diasAdelante)),
    helpText: ayuda,
  );
  return elegido == null ? null : diaOperativoDe(elegido);
}

class SelectorDeDia extends StatelessWidget {
  const SelectorDeDia({
    super.key,
    required this.dia,
    required this.hoy,
    required this.sufijo,
    required this.alIr,
    required this.alCalendario,
    required this.textoDeHoy,
    this.ultimoDia,
  });

  final String dia;
  final String hoy;

  /// Para las claves: `boton_dia_anterior_cargas`, `dia_de_cortes`…
  final String sufijo;
  final void Function(String) alIr;
  final VoidCallback alCalendario;

  /// «Ver las de hoy», «Ver los de hoy».
  final String textoDeHoy;

  /// Hasta dónde se puede ir hacia adelante: los cortes, hasta hoy; las cargas,
  /// sin tope (la carga es de mañana).
  final String? ultimoDia;

  @override
  Widget build(BuildContext context) {
    final alFinal = ultimoDia != null && dia.compareTo(ultimoDia!) >= 0;
    return Column(
      children: [
        Row(
          children: [
            IconButton(
              key: Key('boton_dia_anterior_$sufijo'),
              tooltip: 'Día anterior',
              icon: const Icon(Icons.chevron_left),
              onPressed: () => alIr(moverDia(dia, -1)),
            ),
            Expanded(
              child: Text(
                encabezadoDelDia(dia, hoy: hoy),
                key: Key('dia_de_$sufijo'),
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            IconButton(
              key: Key('boton_dia_siguiente_$sufijo'),
              tooltip: 'Día siguiente',
              icon: const Icon(Icons.chevron_right),
              onPressed: alFinal ? null : () => alIr(moverDia(dia, 1)),
            ),
            IconButton(
              key: Key('boton_calendario_$sufijo'),
              tooltip: 'Elegir el día',
              icon: const Icon(Icons.calendar_month_outlined),
              onPressed: alCalendario,
            ),
          ],
        ),
        if (dia != hoy)
          TextButton(
            key: Key('boton_${sufijo}_de_hoy'),
            onPressed: () => alIr(hoy),
            child: Text(textoDeHoy),
          ),
      ],
    );
  }
}

/// « · para vender el martes 20 de octubre» cuando la carga se vende otro día
/// del que se ve —lo normal: se hace la víspera (ADR 0002 §82, §151)—.
String paraVender(String fecha, {required String dia}) =>
    fecha == dia ? '' : ' · para vender el ${diaEnPalabras(fecha)}';

/// Una etiqueta de color con el estatus: se lee de reojo.
class EtiquetaDeEstatus extends StatelessWidget {
  const EtiquetaDeEstatus({
    super.key,
    required this.texto,
    required this.fondo,
    required this.letra,
  });

  final String texto;
  final Color fondo;
  final Color letra;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        decoration: BoxDecoration(color: fondo, borderRadius: BorderRadius.circular(10)),
        child: Text(
          texto,
          style: TextStyle(color: letra, fontSize: 12, fontWeight: FontWeight.w700),
        ),
      );
}
