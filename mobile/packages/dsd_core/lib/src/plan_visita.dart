/// El plan de visita: qué días le toca a cada cliente.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA MISMA REGLA QUE EN EL SERVIDOR
/// ─────────────────────────────────────────────────────────────────────────
/// Espejo de la función `toca_visita` de la migración 0041. El servidor la usa
/// para contar en Efectividad lo que tocaba y nadie visitó; el teléfono, para
/// decirle al vendedor «hoy te tocan estos». Si las dos discreparan, el vendedor
/// cumpliría su lista y la oficina le reclamaría visitas que su teléfono nunca le
/// pidió.
///
///   · `dia` es el de `extract(dow)`: 0 domingo, 1 lunes … 6 sábado. En Dart
///     `DateTime.weekday` va de 1 (lunes) a 7 (domingo), así que es `% 7`.
///   · `semana` nula es «todas las semanas». Si trae número, vale solo en esa
///     semana del mes: del 1 al 7 es la 1ª, del 8 al 14 la 2ª, y así. Del 29 en
///     adelante es la 5ª, que ningún plan pide: un plan «solo la 4ª semana» no
///     toca el día 30.
library;

import 'dart:convert';

class DiaDeVisita {
  const DiaDeVisita(this.dia, [this.semana]);

  final int dia;
  final int? semana;

  bool tocaEl(DateTime fecha) =>
      fecha.weekday % 7 == dia && (semana == null || semana == semanaDelMes(fecha));

  @override
  bool operator ==(Object other) =>
      other is DiaDeVisita && other.dia == dia && other.semana == semana;

  @override
  int get hashCode => Object.hash(dia, semana);

  @override
  String toString() => 'DiaDeVisita($dia, $semana)';
}

/// La semana del mes, como la cuenta el servidor: `(día − 1) ~/ 7 + 1`.
int semanaDelMes(DateTime fecha) => (fecha.day - 1) ~/ 7 + 1;

/// El plan como llega en el delta del cliente, guardado en `clientes.plan_visita`.
///
/// Tolerante a propósito: un texto que no se entiende es un cliente SIN plan, no
/// una excepción en la lista de clientes. Lo peor que pasa así es que el cliente
/// sale en «Todos» y no en «Hoy»; con una excepción, el vendedor no vería la lista.
List<DiaDeVisita> leerPlanDeVisita(String? json) {
  if (json == null || json.isEmpty) return const [];
  try {
    final crudo = jsonDecode(json);
    if (crudo is! List) return const [];
    return [
      for (final e in crudo)
        if (e is Map && e['dia'] is int)
          DiaDeVisita(e['dia'] as int, e['semana'] is int ? e['semana'] as int : null),
    ];
  } on FormatException {
    return const [];
  }
}

/// Si al cliente le toca visita en esa fecha.
bool tocaVisita(List<DiaDeVisita> plan, DateTime fecha) =>
    plan.any((d) => d.tocaEl(fecha));
