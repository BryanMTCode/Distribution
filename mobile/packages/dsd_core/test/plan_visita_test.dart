/// La regla de «le toca hoy», espejo de `toca_visita` en el servidor (0041).
///
/// Si el teléfono y el servidor discreparan, el vendedor cumpliría su lista y la
/// oficina le reclamaría visitas que su teléfono nunca le pidió. Por eso aquí van
/// los mismos bordes que en SQL: el domingo (0 en la base, 7 en Dart) y la quinta
/// semana del mes, que ningún plan «solo semana N» pide.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:test/test.dart';

void main() {
  // El 5 de octubre de 2026 es lunes.
  final lunes = DateTime(2026, 10, 5);
  final domingo = DateTime(2026, 10, 4);

  group('el día de la semana', () {
    test('lunes es 1, como extract(dow)', () {
      expect(const DiaDeVisita(1).tocaEl(lunes), isTrue);
      expect(const DiaDeVisita(2).tocaEl(lunes), isFalse);
    });

    test('DOMINGO ES 0, aunque Dart diga 7', () {
      expect(domingo.weekday, equals(7));
      expect(const DiaDeVisita(0).tocaEl(domingo), isTrue);
      expect(const DiaDeVisita(6).tocaEl(domingo), isFalse);
    });
  });

  group('las semanas del mes', () {
    test('del 1 al 7 es la 1ª, del 29 en adelante la 5ª', () {
      expect(semanaDelMes(DateTime(2026, 10, 1)), equals(1));
      expect(semanaDelMes(DateTime(2026, 10, 7)), equals(1));
      expect(semanaDelMes(DateTime(2026, 10, 8)), equals(2));
      expect(semanaDelMes(DateTime(2026, 10, 28)), equals(4));
      expect(semanaDelMes(DateTime(2026, 10, 29)), equals(5));
    });

    test('quincenal: lunes de la 1ª y 3ª semana', () {
      const plan = [DiaDeVisita(1, 1), DiaDeVisita(1, 3)];
      expect(tocaVisita(plan, DateTime(2026, 10, 5)), isTrue); // 1ª
      expect(tocaVisita(plan, DateTime(2026, 10, 12)), isFalse); // 2ª
      expect(tocaVisita(plan, DateTime(2026, 10, 19)), isTrue); // 3ª
      expect(tocaVisita(plan, DateTime(2026, 10, 26)), isFalse); // 4ª
    });

    test('un plan «solo la 4ª semana» no toca el día 30', () {
      // El viernes 30 de octubre es la 5ª semana.
      const plan = [DiaDeVisita(5, 4)];
      expect(tocaVisita(plan, DateTime(2026, 10, 23)), isTrue);
      expect(tocaVisita(plan, DateTime(2026, 10, 30)), isFalse);
    });

    test('sin semana es todas las semanas, también la 5ª', () {
      const plan = [DiaDeVisita(5)];
      expect(tocaVisita(plan, DateTime(2026, 10, 30)), isTrue);
    });
  });

  group('lo que llega en el delta', () {
    test('se lee la lista del servidor', () {
      final plan = leerPlanDeVisita('[{"dia":1,"semana":null},{"dia":4,"semana":2}]');
      expect(plan, equals(const [DiaDeVisita(1), DiaDeVisita(4, 2)]));
    });

    test('sin plan, o un texto que no se entiende, es un cliente sin plan', () {
      // Nunca una excepción en la lista de clientes: lo peor es que el cliente
      // salga en «Todos» y no en «Hoy».
      expect(leerPlanDeVisita(null), isEmpty);
      expect(leerPlanDeVisita(''), isEmpty);
      expect(leerPlanDeVisita('[]'), isEmpty);
      expect(leerPlanDeVisita('esto no es json'), isEmpty);
      expect(leerPlanDeVisita('{"dia":1}'), isEmpty);
      expect(tocaVisita(const [], lunes), isFalse);
    });
  });
}
