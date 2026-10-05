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
}

bool _tieneColumna(Database db, String tabla, String columna) {
  // `PRAGMA table_info` no acepta parámetros, así que el nombre va interpolado.
  // Es seguro porque sale de la constante de arriba, nunca de datos.
  final filas = db.select('PRAGMA table_info($tabla)');
  return filas.any((f) => f['name'] == columna);
}
