/// Los folios que se acaban se piden solos al sincronizar (ADR 0002 §171).
///
/// «Te quedan N folios. Sincroniza para pedir más»: y la sincronización no
/// pedía nada. Los folios solo llegaban al vincular el teléfono, así que a las
/// mil ventas el vendedor no podía vender.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

/// El servidor: alarga el rango de venta mil folios y anota lo que le pidieron.
class _Servidor implements Transporte {
  final List<(String, Map<String, Object?>)> pedidas = [];

  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) async =>
      const RespuestaHttp(405, '{}');

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) async {
    pedidas.add((ruta, cuerpo));
    final consumido = (cuerpo['consumido'] as Map<String, Object?>?) ?? {};
    return RespuestaHttp(
      200,
      jsonEncode([
        {
          'documento_tipo': 'venta',
          'desde': 1,
          'hasta': 2000,
          'consumido_hasta': consumido['venta'] ?? 0,
        },
        for (final tipo in ['no_drop', 'merma'])
          {'documento_tipo': tipo, 'desde': 1, 'hasta': 1000, 'consumido_hasta': 0},
      ]),
    );
  }
}

void main() {
  late Database db;
  late RepoFolios repo;

  setUp(() {
    db = sqlite3.openInMemory()..execute(esquemaLocal);
    repo = RepoFolios(db);
  });
  tearDown(() => db.dispose());

  void rango(String tipo, {required int hasta, required int consumido}) => repo.guardar(
        RangoFolios(tipo: tipo, desde: 1, hasta: hasta, consumidoHasta: consumido),
        asignadoEn: '2026-10-22T12:00:00Z',
      );

  Future<bool> pedir(_Servidor servidor) => pedirFoliosSiFaltan(
        transporte: servidor,
        repo: repo,
        dispositivoId: 'equipo-1',
        ahora: '2026-10-22T12:00:00Z',
      );

  test('con folios de sobra no va al servidor', () async {
    for (final tipo in tiposConFolio) {
      rango(tipo, hasta: 1000, consumido: 10);
    }
    final servidor = _Servidor();

    expect(await pedir(servidor), isFalse);
    expect(servidor.pedidas, isEmpty);
  });

  test('con pocos por delante los pide, dice cuántos lleva y guarda el rango largo',
      () async {
    rango('venta', hasta: 1000, consumido: 820);
    rango('no_drop', hasta: 1000, consumido: 10);
    rango('merma', hasta: 1000, consumido: 0);
    final servidor = _Servidor();

    expect(await pedir(servidor), isTrue);

    final (ruta, cuerpo) = servidor.pedidas.single;
    expect(ruta, '/v1/dispositivos/equipo-1/folios');
    expect(cuerpo['consumido'], {'venta': 820, 'no_drop': 10, 'merma': 0});
    // El mismo rango, más largo, sin perder lo consumido.
    final venta = repo.leer('venta')!;
    expect((venta.desde, venta.hasta, venta.consumidoHasta), (1, 2000, 820));
    expect(venta.restantes, 1180);
  });

  test('el tipo sin rango —el vínculo no alcanzó a traerlos— también los pide',
      () async {
    rango('venta', hasta: 1000, consumido: 0);
    rango('merma', hasta: 1000, consumido: 0);
    final servidor = _Servidor();

    expect(await pedir(servidor), isTrue);
    expect(repo.leer('no_drop'), isNotNull);
  });

  test('sin señal el error sube, y lo que había se queda como estaba', () async {
    rango('venta', hasta: 1000, consumido: 990);
    final sinSenal = _SinSenal();

    await expectLater(
      pedirFoliosSiFaltan(
        transporte: sinSenal,
        repo: repo,
        dispositivoId: 'equipo-1',
        ahora: '2026-10-22T12:00:00Z',
      ),
      throwsA(isA<ErrorDeRed>()),
    );
    final venta = repo.leer('venta')!;
    expect((venta.hasta, venta.consumidoHasta), (1000, 990));
  });
}

class _SinSenal implements Transporte {
  @override
  Future<RespuestaHttp> obtener(String ruta, {Map<String, String>? parametros}) =>
      throw const ErrorDeRed('sin señal');

  @override
  Future<RespuestaHttp> post(String ruta, Map<String, Object?> cuerpo) =>
      throw const ErrorDeRed('sin señal');
}
