/// El día de ruta: `YYYY-MM-DD` del calendario **local** del vendedor.
library;

/// La fecha operativa de un instante.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ NO ES `toIso8601String().substring(0, 10)`
/// ───────────────────────────────────────────────────────────────────────────
/// Ese atajo, sobre un `DateTime` en UTC, devuelve el día UTC. En el centro de
/// México son seis horas de diferencia, así que:
///
/// · una venta de las 19:00 del lunes se estampa como del MARTES —01:00 UTC—, y
///   queda fuera del arqueo del lunes: el vendedor entrega un dinero que su
///   liquidación no le reconoce, y al día siguiente aparece un sobrante;
/// · y antes de las 6 de la mañana el mismo atajo apunta al día ANTERIOR.
///
/// O sea que el error no es uniforme: parte del día acierta y parte no, lo que lo
/// vuelve muy difícil de ver. Media ruta cuadra y media no.
///
/// El día operativo es el día del calendario de quien vende. Esta función es el
/// único lugar donde se decide, y los cuatro documentos de campo —venta, cobro,
/// merma, no-drop— la usan: antes cada uno repetía el atajo por su cuenta.
///
/// `toLocal()` usa la zona del teléfono, que es la de la operación. Un teléfono
/// con la zona mal puesta estampa días equivocados, y para eso existe la bandera
/// de reloj desfasado del servidor — que compara su hora con la del equipo.
String diaOperativoDe(DateTime instante) {
  final local = instante.toLocal();
  final mes = local.month.toString().padLeft(2, '0');
  final dia = local.day.toString().padLeft(2, '0');
  return '${local.year}-$mes-$dia';
}
