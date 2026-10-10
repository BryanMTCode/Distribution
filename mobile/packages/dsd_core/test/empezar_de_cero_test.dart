/// Empezar de cero cuando el servidor lo pide (ADR 0002 §90).
///
/// Reporte de la dirección (octubre 2026): tras poner la base en blanco,
/// «aparecen tiendas en la app que se borraron, y en el dashboard no están».
/// El servidor no publica bajas de lo que vació; ahora su pull contesta
/// `resincronizar` y el teléfono olvida su copia y la vuelve a bajar.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

import 'ayudas_sync.dart';

/// Lo que contesta el servidor a un teléfono que tiene que empezar de cero.
Responde empezarDeCero({required bool baseEnBlanco}) => Responde(
      200,
      jsonEncode({
        'cursor': 0,
        'hay_mas': true,
        'cambios': <Object?>[],
        'resincronizar': true,
        'base_en_blanco': baseEnBlanco,
      }),
    );

void main() {
  late Database db;

  setUp(() => db = baseLocal());
  tearDown(() => db.dispose());

  int contar(String tabla) =>
      db.select('SELECT COUNT(*) AS n FROM $tabla').first['n'] as int;

  /// Un teléfono que ya trabajó: la tienda y el artículo de antes, su camión,
  /// una venta ya entregada y su rango de folios.
  Future<void> conDatosDeAntes() async {
    await armarSincronizador(
      db,
      TransporteFalso([
        Responde.pull(cambios: [
          deltaProducto(10, 'p-viejo', 'ATUN-140'),
          deltaCliente(11, 'c-viejo', nombre: 'Abarrotes Lupita'),
        ]),
      ]),
    ).sincronizar(cursorActual: 0);
    db.execute(
      "INSERT INTO existencias_camion (producto_id, cant_actual) VALUES ('p-viejo', 12)",
    );
    db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('almacen_asignado', 'camion-1')",
    );
    db.execute(
      'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, total, '
      '       fecha_dispositivo, fecha_operativa, creado_en) '
      "VALUES ('v-1', 5, 'VEND01-000005', 'c-viejo', 592, 'x', '2026-10-07', 'x')",
    );
    db.execute(
      'INSERT INTO outbox (operacion_id, tipo, entidad_id, payload, hash_payload, '
      '       secuencia, estado, creado_en, confirmado_en) '
      "VALUES ('op-v1', 'venta.crear', 'v-1', '{}', 'h', 0, 'confirmada', 'x', 'x')",
    );
    db.execute("INSERT INTO familias (id, nombre, orden) VALUES ('f-1', 'Atunes', 1)");
    db.execute(
      'INSERT INTO folios_rangos (tipo, desde, hasta, consumido_hasta) '
      "VALUES ('venta', 1, 100, 5)",
    );
  }

  test('tras la base en blanco, la tienda de antes se va y entra lo nuevo', () async {
    await conDatosDeAntes();
    final transporte = TransporteFalso([
      empezarDeCero(baseEnBlanco: true),
      Responde.pull(cambios: [deltaProducto(500, 'p-nuevo', 'S-100')]),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 11);

    expect(r.empezoDeCero, isTrue);
    expect(r.fin, FinDeSync.completa);
    expect(r.cursor, 500);
    // Pidió desde su cursor, le dijeron que empezara de cero, y pidió desde 0.
    final pulls = transporte.llamadas.where((l) => l.contains('pull')).toList();
    expect(pulls, [contains('cursor=11'), contains('cursor=0')]);

    expect(contar('clientes'), 0, reason: 'la tienda borrada seguía en la app');
    expect(
      db.select('SELECT sku FROM productos').map((f) => f['sku']),
      ['S-100'],
    );
    expect(contar('existencias_camion'), 0);
    expect(contar('familias'), 0);
    expect(contar('ventas'), 0, reason: 'el servidor ya no tiene esa venta');
    expect(contar('outbox'), 0);
    expect(
      db.select("SELECT 1 FROM sync_estado WHERE clave = 'almacen_asignado'"),
      isEmpty,
    );
    // Lo que no se toca: el rango de folios —un folio impreso no se repite—.
    expect(
      db.select('SELECT consumido_hasta FROM folios_rangos').single['consumido_hasta'],
      5,
    );
    expect(db.select('PRAGMA foreign_keys').single.values.first, 1);
  });

  test('tras una poda se vuelve a bajar el espejo, pero las ventas de hoy se quedan',
      () async {
    await conDatosDeAntes();
    final transporte = TransporteFalso([
      empezarDeCero(baseEnBlanco: false),
      Responde.pull(cambios: [
        deltaProducto(900, 'p-viejo', 'ATUN-140'),
        deltaCliente(901, 'c-viejo', nombre: 'Abarrotes Lupita'),
      ]),
    ]);

    final r = await armarSincronizador(db, transporte).sincronizar(cursorActual: 11);

    expect(r.empezoDeCero, isTrue);
    expect(r.cursor, 901);
    expect(contar('ventas'), 1);
    expect(contar('outbox'), 1);
    expect(contar('clientes'), 1);
    expect(contar('productos'), 1);
    // El camión sí se vacía: es espejo, y lo repone el cuadre o la carga.
    expect(contar('existencias_camion'), 0);
    expect(db.select('PRAGMA foreign_keys').single.values.first, 1);
  });

  test('con cola pendiente no se olvida nada: primero se entrega', () async {
    await conDatosDeAntes();
    final outbox = Outbox(db);
    encolarAlta(outbox, db, 'c-nuevo-1', secuencia: 1);
    encolarAlta(outbox, db, 'c-nuevo-2', secuencia: 2);
    final transporte = TransporteFalso([
      const AceptaLoEnviado(),
      empezarDeCero(baseEnBlanco: true),
    ]);
    // Una tanda de un sobre: el segundo se queda en la cola.
    final sincronizador = Sincronizador(
      outbox: outbox,
      cliente: ClienteSync(transporte),
      aplicador: AplicadorDeltas(db),
      ahora: () => ahoraFijo,
      maxSobresPorLote: 1,
      maxTandas: 1,
    );

    final r = await sincronizador.sincronizar(cursorActual: 11);

    expect(r.fin, FinDeSync.parcial);
    expect(r.empezoDeCero, isFalse);
    expect(r.cursor, 11, reason: 'el servidor lo vuelve a pedir en la siguiente');
    expect(outbox.resumen().pendientes, 1);
    expect(contar('ventas'), 1);
    expect(
      db.select("SELECT 1 FROM clientes WHERE id = 'c-viejo'"),
      isNotEmpty,
    );
  });

  test('un servidor viejo no manda resincronizar y nada cambia', () {
    final r = RespuestaPull.deJson({'cursor': 3, 'hay_mas': false, 'cambios': []});
    expect(r.resincronizar, isFalse);
    expect(r.baseEnBlanco, isFalse);
  });
}
