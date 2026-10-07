/// Lo que el servidor confirmó queda marcado como subido en el teléfono.
///
/// Bug de campo, octubre 2026: el sobre salía de la cola pero la venta se quedaba
/// con `sincronizada = 0`, y «Mi día» pedía sincronizar una venta que la oficina
/// ya tenía en el panel.
library;

import 'dart:convert';

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

void main() {
  late Database db;
  late Outbox outbox;

  setUp(() {
    db = sqlite3.openInMemory();
    db.execute(esquemaLocal);
    outbox = Outbox(db);
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial) VALUES ('cli-1', 'La Esquina')",
    );
  });

  tearDown(() => db.dispose());

  /// Un sobre de visita: venta y cobro juntos, como los manda la app.
  void encolarVisita(String operacionId) {
    final sobre = SobreLocal(
      operacionId: operacionId,
      secuencia: outbox.siguienteSecuencia(),
      visitaId: 'visita-1',
      operaciones: const [
        OperacionLocal(tipo: 'venta.crear', entidadId: 'v-1', datos: {}),
        OperacionLocal(tipo: 'cobro.crear', entidadId: 'k-1', datos: {}),
      ],
    );
    outbox.encolar(
      sobre,
      creadoEn: '2026-10-07T18:00:00Z',
      escribirNegocio: (d) {
        d.execute(
          'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, total, '
          '       fecha_dispositivo, fecha_operativa, creado_en) '
          "VALUES ('v-1', 1, 'VEND01-000001', 'cli-1', 592, 'x', '2026-10-07', 'x')",
        );
        d.execute(
          'INSERT INTO cobros (id, folio_consecutivo, folio_local, cliente_id, importe, '
          '       forma_pago, fecha_dispositivo, fecha_operativa, creado_en) '
          "VALUES ('k-1', 1, 'VEND01-C000001', 'cli-1', 100, 'efectivo', 'x', '2026-10-07', 'x')",
        );
      },
    );
  }

  int marca(String sql) => db.select(sql).single.values.first! as int;

  test('al confirmar, la venta y el cobro del sobre quedan como subidos', () {
    encolarVisita('op-1');
    expect(marca("SELECT sincronizada FROM ventas WHERE id = 'v-1'"), equals(0));

    outbox.confirmar(['op-1'], confirmadoEn: '2026-10-07T18:01:00Z');

    expect(marca("SELECT sincronizada FROM ventas WHERE id = 'v-1'"), equals(1));
    expect(marca("SELECT sincronizado FROM cobros WHERE id = 'k-1'"), equals(1));
  });

  test('lo que se confirmó con la versión anterior también se corrige', () {
    encolarVisita('op-1');
    // Como lo dejaba la versión anterior: confirmado en la cola, venta sin marcar.
    db.execute("UPDATE outbox SET estado = 'confirmada'");

    outbox.marcarDocumentosConfirmados();

    expect(marca("SELECT sincronizada FROM ventas WHERE id = 'v-1'"), equals(1));
  });

  test('lo que sigue pendiente no se marca', () {
    encolarVisita('op-1');
    outbox.marcarDocumentosConfirmados();
    expect(marca("SELECT sincronizada FROM ventas WHERE id = 'v-1'"), equals(0));
    // El payload es el de siempre: la marca sale de lo que el sobre declara.
    final payload = jsonDecode(
      db.select('SELECT payload FROM outbox').single['payload'] as String,
    ) as Map<String, Object?>;
    expect((payload['operaciones']! as List).length, equals(2));
  });
}
