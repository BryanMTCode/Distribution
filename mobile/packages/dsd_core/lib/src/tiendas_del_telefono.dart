/// Las tiendas que el teléfono tiene de más se sueltan solas (ADR 0002 §174).
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ EL TELÉFONO PREGUNTA
/// ───────────────────────────────────────────────────────────────────────────
/// «En todos los días de la semana me aparecen como 90 y tantas tiendas», y la
/// 1107 trae 46 el lunes. El teléfono no borra tiendas por su cuenta: solo las
/// deja inactivas cuando el servidor le manda la baja. Y de lo que se tiró con
/// la base en blanco el servidor ya no sabe nada: no hay baja que mandar. Un
/// teléfono que se vinculó de nuevo sin empezar de cero se quedó con la ruta de
/// antes y la de ahora, cada día al doble, y con ventas «a un cliente que no
/// existe».
///
/// Así que manda los ids de las tiendas que enseña y el servidor le contesta
/// cuáles no son suyas. Se dejan inactivas, igual que con una baja: sus ventas
/// sin subir siguen en pie (ver `_cliente` en `aplicador_deltas.dart`).
library;

import 'dart:convert';

import 'package:sqlite3/sqlite3.dart';

import 'sync_cliente.dart' show ServidorConProblemas;
import 'transporte.dart';

/// Pregunta cuáles de las tiendas que enseña no son suyas, y las suelta.
///
/// Devuelve cuántas soltó, o nulo si el servidor todavía no sabe contestar
/// (anterior a la migración 0069): entonces no cambia nada. Las altas que
/// todavía no suben (`es_local`) ni se mandan ni se tocan: el servidor no las
/// conoce y no son ajenas. Un `ErrorDeRed` o un [ServidorConProblemas] suben
/// tal cual; quien llama lo intenta en la siguiente sincronización.
Future<int?> soltarTiendasAjenas({
  required Transporte transporte,
  required Database db,
}) async {
  final tiene = [
    for (final f in db.select('SELECT id FROM clientes WHERE activo = 1 AND es_local = 0'))
      f['id'] as String,
  ];
  final respuesta = await transporte.post('/v1/sync/tiendas', {'tiene': tiene});
  if (respuesta.codigo == 404 || respuesta.codigo == 405) return null;
  if (!respuesta.ok) throw ServidorConProblemas(respuesta.codigo, respuesta.cuerpo);

  // Solo de las que mandó: el servidor contesta en minúsculas, y un id que no
  // venía en la lista no tiene nada que hacer aquí.
  final porId = {for (final id in tiene) id.toLowerCase(): id};
  final soltar = [
    for (final id in ((jsonDecode(respuesta.cuerpo) as Map)['soltar'] as List).cast<String>())
      if (porId[id.toLowerCase()] case final local?) local,
  ];
  if (soltar.isEmpty) return 0;

  final cambio = db.prepare('UPDATE clientes SET activo = 0 WHERE id = ? AND es_local = 0');
  db.execute('BEGIN IMMEDIATE');
  try {
    for (final id in soltar) {
      cambio.execute([id]);
    }
    db.execute('COMMIT');
  } catch (_) {
    db.execute('ROLLBACK');
    rethrow;
  } finally {
    cambio.dispose();
  }
  return soltar.length;
}
