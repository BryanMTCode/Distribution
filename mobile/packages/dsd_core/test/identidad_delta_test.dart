/// El camión que la oficina le asignó, aplicado en el teléfono.
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ DEFIENDE ESTE ARCHIVO, Y QUÉ NO HACÍA FALTA DEFENDER
/// ───────────────────────────────────────────────────────────────────────────
/// Lo que la auditoría de octubre de 2026 anotó como riesgo —que las ventas
/// salieran estampadas con el camión viejo— **no podía pasar**: el servidor ignora
/// el `almacen_id` del payload y usa el del token. Eso ya estaba resuelto, y la
/// corrección quedó escrita en el documento de la auditoría.
///
/// El hueco real es el inventario LOCAL. `existencias_camion` es «mi camión»,
/// implícito, y nada la reinicia **por diseño**: el camión es un almacén rodante y
/// su saldo se arrastra de un día al siguiente. Reasignar el camión es el único
/// evento que tiene que reiniciarlo, o el teléfono mezcla el sobrante del camión
/// viejo con las cargas del nuevo y le ofrece al cliente mercancía que está en otro
/// vehículo.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';
import 'package:test/test.dart';

const _camionViejo = 'almacen-viejo';
const _camionNuevo = 'almacen-nuevo';
const _atun = 'p-atun';

void main() {
  late Database db;
  late AplicadorDeltas aplicador;

  setUp(() {
    db = sqlite3.openInMemory();
    db.execute('PRAGMA foreign_keys = ON');
    aplicarEsquemaLocal(db);
    aplicador = AplicadorDeltas(db);

    db.execute(
      "INSERT INTO productos (id, sku, nombre, unidad_base) "
      "VALUES (?, 'ATUN-140', 'Atún', 'PZA')",
      [_atun],
    );
  });

  tearDown(() => db.dispose());

  Delta identidad(String almacen) => Delta(
        cursor: 1,
        entidad: 'identidad',
        entidadId: 'u-vendedor',
        operacion: 'upsert',
        payload: {'usuario_id': 'u-vendedor', 'almacen_id': almacen},
      );

  void aplicar(Delta d) =>
      aplicador.aplicar([d], recibidoEn: '2026-10-06T10:00:00.000Z');

  String? almacenGuardado() {
    final f = db.select(
      "SELECT valor FROM sync_estado WHERE clave = 'almacen_asignado'",
    );
    return f.isEmpty ? null : f.single['valor'] as String?;
  }

  /// Un camión con mercancía, su carga aplicada y su carga activa.
  void conCamionCargado() {
    db.execute(
      'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, '
      "                                carga_id) VALUES (?, 240, 120, 'carga-1')",
      [_atun],
    );
    db.execute(
      "INSERT INTO cargas_aplicadas (carga_id, aplicada_en) "
      "VALUES ('carga-1', '2026-10-05T07:00:00Z')",
    );
    db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('carga_id_activa', 'carga-1')",
    );
  }

  int cuantos(String tabla) =>
      db.select('SELECT count(*) AS n FROM $tabla').single['n'] as int;

  // -------------------------------------------------------------------------

  test('guarda el camión asignado', () {
    aplicar(identidad(_camionViejo));
    expect(almacenGuardado(), equals(_camionViejo));
  });

  test('el primer delta NO le vacía el camión al teléfono recién vinculado', () {
    // Un teléfono que acaba de vincularse ya tiene su carga del día aplicada y
    // todavía no ha recibido ningún delta de identidad. Vaciarle el camión por
    // «cambió de null a algo» lo dejaría sin inventario a media mañana.
    conCamionCargado();

    aplicar(identidad(_camionViejo));

    expect(cuantos('existencias_camion'), equals(1));
    expect(cuantos('cargas_aplicadas'), equals(1));
  });

  test('el MISMO camión otra vez no vacía nada', () {
    // Un `pull` repetido trae el mismo delta. Si cada llegada vaciara el camión,
    // el vendedor se quedaría sin inventario cada vez que sincroniza.
    aplicar(identidad(_camionViejo));
    conCamionCargado();

    aplicar(identidad(_camionViejo));

    expect(cuantos('existencias_camion'), equals(1));
    expect(
      db.select('SELECT cant_actual FROM existencias_camion').single['cant_actual'],
      equals(120.0),
    );
  });

  test('UN CAMIÓN DISTINTO REINICIA EL INVENTARIO LOCAL', () {
    aplicar(identidad(_camionViejo));
    conCamionCargado();

    aplicar(identidad(_camionNuevo));

    expect(almacenGuardado(), equals(_camionNuevo));
    expect(
      cuantos('existencias_camion'),
      equals(0),
      reason: 'el sobrante del camión viejo seguía ofreciéndose en el nuevo',
    );
    expect(cuantos('cargas_aplicadas'), equals(0));
    expect(
      db.select("SELECT * FROM sync_estado WHERE clave = 'carga_id_activa'"),
      isEmpty,
      reason: 'las ventas quedarían amarradas a la carga de otro camión',
    );
  });

  test('cambiar de camión NO se lleva los documentos sin subir', () {
    // Describen lo que pasó en la calle, y lo que pasó no cambia porque la
    // oficina le haya cambiado el camión. Si se perdieran, el vendedor entregaría
    // dinero que el sistema no sabe que cobró.
    db.execute(
      "INSERT INTO clientes (id, nombre_comercial) VALUES ('cli-1', 'La Esquina')",
    );
    db.execute(
      'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, total, '
      '       fecha_dispositivo, fecha_operativa, sincronizada, creado_en) '
      "VALUES ('v-1', 1, 'VEND01-000001', 'cli-1', 592, 'x', '2026-10-06', 0, 'x')",
    );
    aplicar(identidad(_camionViejo));
    conCamionCargado();

    aplicar(identidad(_camionNuevo));

    expect(cuantos('ventas'), equals(1));
  });

  test('la identidad no cae en deltas_desconocidos', () {
    final r = aplicador.aplicar(
      [identidad(_camionViejo)],
      recibidoEn: '2026-10-06T10:00:00.000Z',
    );
    expect(r.aplicados, equals(1));
    expect(r.desconocidos, equals(0));
    expect(r.fallidos, equals(0));
  });

  Delta sinCamion() => Delta(
        cursor: 2,
        entidad: 'identidad',
        entidadId: 'u-vendedor',
        operacion: 'upsert',
        payload: {'usuario_id': 'u-vendedor', 'almacen_id': null},
      );

  test('QUITARLE EL CAMIÓN LO DEJA SIN CAMIÓN, no con el de antes', () {
    // La oficina le pasó el camión a otro vendedor, o lo dio de baja. El delta
    // llega con `almacen_id` nulo, solo a ESTE teléfono (el servidor lo acota a
    // su dueño). Antes se ignoraba, y el teléfono seguía ofreciendo la mercancía
    // de un camión que ya manejaba otro.
    aplicar(identidad(_camionViejo));
    conCamionCargado();

    aplicar(sinCamion());

    expect(
      db.select("SELECT valor FROM sync_estado WHERE clave = 'almacen_asignado'"),
      hasLength(1),
      reason: 'la fila con valor nulo es «sin camión»; sin fila, el teléfono '
          'volvería al camión de la credencial',
    );
    expect(almacenGuardado(), isNull);
    expect(cuantos('existencias_camion'), equals(0));
    expect(cuantos('cargas_aplicadas'), equals(0));
  });

  test('sin camión y luego uno nuevo: queda el nuevo, sin vaciar lo que ya trae', () {
    aplicar(identidad(_camionViejo));
    aplicar(sinCamion());
    conCamionCargado();

    aplicar(identidad(_camionNuevo));

    expect(almacenGuardado(), equals(_camionNuevo));
    expect(cuantos('existencias_camion'), equals(1));
  });

  test('un teléfono recién vinculado que recibe «sin camión» no pierde nada', () {
    conCamionCargado();
    aplicar(sinCamion());
    expect(cuantos('existencias_camion'), equals(1));
  });
}
