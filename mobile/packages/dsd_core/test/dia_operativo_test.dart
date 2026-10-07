/// El día de ruta es el día LOCAL, no el de UTC.
///
/// Este archivo existe por un fallo de dinero: `fecha_operativa` se derivaba con
/// `toIso8601String().substring(0, 10)` sobre un instante en UTC, y en UTC−6 eso
/// manda las ventas de la tarde al día siguiente — fuera del arqueo del día en que
/// se hicieron.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  test('una venta de las 19:00 pertenece a SU día, no al siguiente', () {
    // 19:00 del 5 de octubre en el centro de México son 01:00 UTC del día 6. El
    // atajo de UTC la habría estampado como del 6: el vendedor entrega el dinero
    // el 5, su liquidación del 5 no la reconoce, y el 6 aparece un sobrante que
    // nadie puede explicar.
    final tarde = DateTime(2026, 10, 5, 19);

    expect(diaOperativoDe(tarde), equals('2026-10-05'));
  });

  test('una venta de las 5 de la mañana tampoco se va al día anterior', () {
    // El error no es uniforme, y eso es lo que lo hace difícil de ver: antes de
    // las 6 am el mismo atajo apunta al día ANTERIOR. Media ruta cuadraba y media
    // no.
    final madrugada = DateTime(2026, 10, 5, 5, 30);

    expect(diaOperativoDe(madrugada), equals('2026-10-05'));
  });

  test('da el día local aunque el instante venga en UTC', () {
    // Es como llega: los documentos guardan `fecha_dispositivo` en UTC, que es lo
    // correcto para un instante. La fecha operativa se deriva del mismo instante
    // pero en el calendario de quien vende.
    final enUtc = DateTime.utc(2026, 10, 6, 1, 0);

    expect(diaOperativoDe(enUtc), equals(diaOperativoDe(enUtc.toLocal())));
  });

  test('rellena mes y día con cero', () {
    // Un '2026-1-5' no es comparable con '2026-01-05' en SQLite, y la fecha
    // operativa se compara como TEXTO en todas las consultas del día.
    expect(diaOperativoDe(DateTime(2026, 1, 5, 12)), equals('2026-01-05'));
  });

  test('el día se dice con su nombre, y hoy y ayer se nombran', () {
    expect(diaEnPalabras('2026-10-07'), 'miércoles 7 de octubre');
    expect(encabezadoDelDia('2026-10-07', hoy: '2026-10-07'), 'Hoy, miércoles 7 de octubre');
    expect(encabezadoDelDia('2026-10-06', hoy: '2026-10-07'), 'Ayer, martes 6 de octubre');
    expect(encabezadoDelDia('2026-10-01', hoy: '2026-10-07'), 'Jueves 1 de octubre');
    // El primero de mes: «ayer» es el último del mes anterior.
    expect(encabezadoDelDia('2026-09-30', hoy: '2026-10-01'), 'Ayer, miércoles 30 de septiembre');
  });
}
