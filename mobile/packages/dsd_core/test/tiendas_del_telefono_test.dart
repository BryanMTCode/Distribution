/// Las tiendas que el teléfono tiene de más se sueltan solas (ADR 0002 §174).
///
/// «En todos los días de la semana me aparecen como 90 y tantas tiendas»: la
/// ruta dos veces, la que se tiró con la base en blanco y la de ahora. Del
/// servidor no podía llegar la baja de lo que ya no conoce; ahora el teléfono
/// pregunta.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

/// El servidor: contesta que no son suyas las que le digan, y anota lo que le
/// mandaron.
class _Servidor implements Transporte {
  _Servidor({this.ajenas = const {}, this.codigo = 200});

  final Set<String> ajenas;
  final int codigo;
  final List<List<String>> mandadas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      const RespuestaHttp(405, '{}');

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    expect(ruta, '/v1/sync/tiendas');
    final tiene = (cuerpo['tiene']! as List).cast<String>();
    mandadas.add(tiene);
    if (codigo != 200) return RespuestaHttp(codigo, '{"detail":"Not Found"}');
    return RespuestaHttp(
      200,
      jsonEncode({
        'soltar': [
          for (final id in tiene)
            if (ajenas.contains(id)) id.toLowerCase(),
          // Uno que el teléfono no mandó: no tiene nada que hacer aquí.
          '00000000-0000-4000-8000-000000000000',
        ],
      }),
    );
  }
}

void main() {
  late Database db;

  setUp(() => db = sqlite3.openInMemory()..execute(esquemaLocal));
  tearDown(() => db.dispose());

  void tienda(String id, {bool activa = true, bool local = false}) => db.execute(
        'INSERT INTO clientes (id, nombre_comercial, activo, es_local) VALUES (?, ?, ?, ?)',
        [id, 'Abarrotes $id', activa ? 1 : 0, local ? 1 : 0],
      );

  List<String> activas() => [
        for (final f in db.select('SELECT id FROM clientes WHERE activo = 1 ORDER BY id'))
          f['id'] as String,
      ];

  test('suelta las que el servidor dice que no son suyas, y solo ésas', () async {
    tienda('a-suya');
    tienda('b-vieja');
    tienda('c-otra-ruta');
    tienda('d-ya-inactiva', activa: false);
    tienda('e-alta-sin-subir', local: true);
    final servidor = _Servidor(ajenas: {'b-vieja', 'c-otra-ruta'});

    final soltadas = await soltarTiendasAjenas(transporte: servidor, db: db);

    expect(soltadas, 2);
    // Manda las que enseña y el servidor conoce: ni la inactiva ni el alta
    // que todavía no sube.
    expect(servidor.mandadas.single..sort(), ['a-suya', 'b-vieja', 'c-otra-ruta']);
    expect(activas(), ['a-suya', 'e-alta-sin-subir']);
  });

  test('con todas suyas no cambia nada', () async {
    tienda('a-suya');
    expect(await soltarTiendasAjenas(transporte: _Servidor(), db: db), 0);
    expect(activas(), ['a-suya']);
  });

  test('el servidor que todavía no sabe contestar no cambia nada', () async {
    tienda('a-suya');
    tienda('b-vieja');

    final soltadas = await soltarTiendasAjenas(
      transporte: _Servidor(ajenas: {'b-vieja'}, codigo: 404),
      db: db,
    );

    expect(soltadas, isNull);
    expect(activas(), ['a-suya', 'b-vieja']);
  });

  test('un error del servidor sube, y no se suelta nada', () async {
    tienda('b-vieja');
    await expectLater(
      soltarTiendasAjenas(transporte: _Servidor(ajenas: {'b-vieja'}, codigo: 500), db: db),
      throwsA(isA<ServidorConProblemas>()),
    );
    expect(activas(), ['b-vieja']);
  });

  test('la tienda soltada vuelve si el servidor la manda otra vez', () async {
    tienda('11111111-1111-4111-8111-111111111111');
    await soltarTiendasAjenas(
      transporte: _Servidor(ajenas: {'11111111-1111-4111-8111-111111111111'}),
      db: db,
    );
    expect(activas(), isEmpty);

    // La oficina se la pasa a su ruta: llega el alta de siempre.
    final r = AplicadorDeltas(db).aplicar([
      Delta(
        cursor: 1,
        entidad: 'cliente',
        entidadId: '11111111-1111-4111-8111-111111111111',
        operacion: 'upsert',
        payload: {
          'id': '11111111-1111-4111-8111-111111111111',
          'nombre_comercial': 'Abarrotes',
          'estatus': 'activo',
          'permite_credito': false,
          'limite_credito': '0.00',
          'bloqueado': false,
        },
      ),
    ], recibidoEn: '2026-10-22T12:00:00Z');
    expect(r.fallidos, 0, reason: db.select('SELECT error FROM deltas_desconocidos').toString());
    expect(activas(), ['11111111-1111-4111-8111-111111111111']);
  });
}
