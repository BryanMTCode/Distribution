/// Poner al día la base de un teléfono que YA estaba instalado.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ `esquemaLocal` NO ALCANZA, Y CUÁNDO SE DESCUBRE
/// ───────────────────────────────────────────────────────────────────────────
/// El esquema local se aplica entero en CADA arranque y es idempotente: todas sus
/// sentencias son `CREATE ... IF NOT EXISTS`. Eso funciona para una tabla nueva —
/// aparece sola la próxima vez que la app abre— y **no funciona para una columna
/// nueva en una tabla que ya existe**: `CREATE TABLE IF NOT EXISTS` ve la tabla,
/// no hace nada, y la columna nunca llega.
///
/// La forma en que eso se descubre es la peor posible: la app se instala sobre una
/// versión anterior, arranca bien, y reventá días después al escribir la columna
/// que falta — en la calle, en la pantalla que la usa, con el cliente enfrente.
///
/// Así que cada columna que llega después de su tabla se anota aquí, con el
/// `ALTER TABLE` que la agrega. Se aplica solo si falta, preguntándole a la base
/// qué columnas tiene; no se confía en una versión guardada, porque una base
/// reinstalada a medias tendría la versión nueva y las columnas viejas.
library;

import 'package:sqlite3/sqlite3.dart';

import 'esquema_local.dart';

/// Columna que nació después de su tabla: `tabla → (columna, ALTER)`.
///
/// Se agregan al final de la lista y **no se borran nunca**, aunque el esquema ya
/// las traiga: un teléfono que lleva meses sin actualizarse sigue necesitando la
/// de hace seis versiones.
const _columnasQueLlegaronDespues = <(String, String, String)>[
  // La nota de la oficina cuando cancela o corrige una venta (octubre 2026).
  ('ventas', 'nota_oficina', 'ALTER TABLE ventas ADD COLUMN nota_oficina TEXT'),
  // La baja de un cliente, que antes se intentaba con un DELETE y mataba la
  // sincronización (auditoría de octubre 2026, hallazgo 1).
  (
    'clientes',
    'activo',
    'ALTER TABLE clientes ADD COLUMN activo INTEGER NOT NULL DEFAULT 1',
  ),
  // Por qué un delta no se pudo aplicar (hallazgo 2).
  (
    'deltas_desconocidos',
    'error',
    'ALTER TABLE deltas_desconocidos ADD COLUMN error TEXT',
  ),
  // Lo que el cliente pagó por transferencia o cheque y la oficina todavía no
  // confirma (octubre 2026, migración 0038 del servidor).
  (
    'clientes',
    'por_confirmar',
    'ALTER TABLE clientes ADD COLUMN por_confirmar REAL NOT NULL DEFAULT 0',
  ),
  // Los días de visita del cliente (octubre 2026, migración 0041 del servidor).
  ('clientes', 'plan_visita', 'ALTER TABLE clientes ADD COLUMN plan_visita TEXT'),
  // Solo contado (octubre 2026, migración 0046 del servidor): la venta guarda
  // cómo se pagó. Lo vendido antes de esta versión era efectivo.
  (
    'ventas',
    'forma_pago',
    "ALTER TABLE ventas ADD COLUMN forma_pago TEXT NOT NULL DEFAULT 'efectivo'",
  ),
  ('ventas', 'referencia_pago', 'ALTER TABLE ventas ADD COLUMN referencia_pago TEXT'),
  (
    'carrito_borrador',
    'forma_pago',
    "ALTER TABLE carrito_borrador ADD COLUMN forma_pago TEXT NOT NULL DEFAULT 'efectivo'",
  ),
  (
    'carrito_borrador',
    'referencia_pago',
    'ALTER TABLE carrito_borrador ADD COLUMN referencia_pago TEXT',
  ),
  // La visita reprogramada (octubre 2026, migración 0054 del servidor).
  (
    'clientes',
    'visita_reprogramada',
    'ALTER TABLE clientes ADD COLUMN visita_reprogramada TEXT',
  ),
  (
    'no_drops',
    'reprogramada_para',
    'ALTER TABLE no_drops ADD COLUMN reprogramada_para TEXT',
  ),
  // Qué cambió en cada ajuste de la oficina, para decírselo al vendedor (ADR
  // 0002 §145).
  (
    'ajustes_camion_aplicados',
    'producto_id',
    'ALTER TABLE ajustes_camion_aplicados ADD COLUMN producto_id TEXT',
  ),
  (
    'ajustes_camion_aplicados',
    'delta',
    'ALTER TABLE ajustes_camion_aplicados ADD COLUMN delta REAL',
  ),
];

/// Aplica el esquema y las columnas que llegaron después.
///
/// Es lo que tiene que llamar cualquiera que abra la base: la app al arrancar y
/// las pruebas al montar una base nueva. En una base recién creada los `ALTER` no
/// hacen nada —el esquema ya trae las columnas—, y ése es justamente el caso que
/// no se puede probar sin fingir una base vieja. Ver `esquema_al_dia_test.dart`.
void aplicarEsquemaLocal(Database db) {
  db.execute(esquemaLocal);
  for (final (tabla, columna, alter) in _columnasQueLlegaronDespues) {
    if (!_tieneColumna(db, tabla, columna)) {
      db.execute(alter);
    }
  }
  _noDropSinGpsObligatorio(db);
}

/// El no-drop sin GPS (ADR 0002 §158): en un teléfono que ya estaba instalado,
/// `no_drops.lat` y `lng` traen `NOT NULL`, y SQLite no deja quitarlo con un
/// `ALTER`. Se rehace la tabla con el esquema de hoy y los mismos renglones, en el
/// orden que recomienda SQLite —la nueva, copiar, borrar la vieja, renombrar—, y
/// solo una vez: después la columna ya no es obligatoria.
///
/// Con las llaves foráneas apagadas mientras se copia, también como lo recomienda
/// SQLite: un no-drop de hace meses que apunte a un motivo que ya no está en el
/// catálogo haría fallar la copia, y el teléfono no arrancaría.
void _noDropSinGpsObligatorio(Database db) {
  final lat = db.select('PRAGMA table_info(no_drops)').where((f) => f['name'] == 'lat');
  if (lat.isEmpty || lat.single['notnull'] != 1) return;

  final crear = RegExp(r'CREATE TABLE IF NOT EXISTS no_drops \([\s\S]*?\n\);')
      .firstMatch(esquemaLocal)!
      .group(0)!
      .replaceFirst('no_drops (', 'no_drops_sin_gps_obligatorio (');
  final columnas = [
    for (final f in db.select('PRAGMA table_info(no_drops)')) f['name'] as String,
  ].join(', ');
  final llaves = db.select('PRAGMA foreign_keys').single.values.first;
  db.execute('PRAGMA foreign_keys = OFF');
  db.execute('SAVEPOINT no_drop_sin_gps');
  try {
    db.execute(crear);
    db.execute(
      'INSERT INTO no_drops_sin_gps_obligatorio ($columnas) SELECT $columnas FROM no_drops',
    );
    db.execute('DROP TABLE no_drops');
    db.execute('ALTER TABLE no_drops_sin_gps_obligatorio RENAME TO no_drops');
    db.execute('RELEASE no_drop_sin_gps');
  } catch (_) {
    db.execute('ROLLBACK TO no_drop_sin_gps');
    db.execute('RELEASE no_drop_sin_gps');
    rethrow;
  } finally {
    db.execute('PRAGMA foreign_keys = ${llaves == 1 ? 'ON' : 'OFF'}');
  }
}

bool _tieneColumna(Database db, String tabla, String columna) {
  // `PRAGMA table_info` no acepta parámetros, así que el nombre va interpolado.
  // Es seguro porque sale de la constante de arriba, nunca de datos.
  final filas = db.select('PRAGMA table_info($tabla)');
  return filas.any((f) => f['name'] == columna);
}
