/// Aplica los deltas del servidor al espejo local.
///
/// La zona ESPEJO (catálogo, precios, clientes, cartera) se sobrescribe con lo
/// que manda el servidor: él es el dueño y siempre gana, así que no hay
/// conflictos que resolver. Se usa `INSERT ... ON CONFLICT DO UPDATE` para que
/// aplicar el mismo delta dos veces sea inofensivo — el pull puede repetirse
/// tras un corte y no debe hacer daño.
library;

import 'dart:convert';

import 'package:sqlite3/sqlite3.dart';

import 'sync_cliente.dart';

class ResultadoAplicacion {
  const ResultadoAplicacion({
    required this.aplicados,
    required this.desconocidos,
  });

  final int aplicados;

  /// Entidades que esta versión de la app no sabe aplicar. Se guardan crudas;
  /// ver la tabla `deltas_desconocidos`.
  final int desconocidos;
}

class AplicadorDeltas {
  const AplicadorDeltas(this._db);

  final Database _db;

  /// Aplica una tanda completa en una transacción.
  ///
  /// O entra toda o no entra nada: un catálogo a medias —productos sin sus
  /// precios— haría que el vendedor viera artículos que no puede cotizar.
  ResultadoAplicacion aplicar(List<Delta> deltas, {required String recibidoEn}) {
    var aplicados = 0;
    var desconocidos = 0;

    _db.execute('BEGIN IMMEDIATE');
    try {
      for (final delta in deltas) {
        final manejado = _aplicarUno(delta, recibidoEn);
        if (manejado) {
          aplicados++;
        } else {
          desconocidos++;
          _guardarDesconocido(delta, recibidoEn);
        }
      }
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }

    return ResultadoAplicacion(aplicados: aplicados, desconocidos: desconocidos);
  }

  bool _aplicarUno(Delta delta, String recibidoEn) => switch (delta.entidad) {
        'producto' => _producto(delta),
        'producto_unidad' => _productoUnidad(delta),
        'precio' => _precio(delta),
        'cliente' => _cliente(delta),
        'cartera' => _cartera(delta, recibidoEn),
        // El dispositivo no lleva listas de precios ni promociones todavía: el
        // cliente trae su lista_precios_id y con eso resuelve el precio.
        'lista_precios' || 'promocion' || 'carga' => true,
        _ => false,
      };

  void _guardarDesconocido(Delta delta, String recibidoEn) {
    _db.execute(
      '''
      INSERT INTO deltas_desconocidos (cursor, entidad, entidad_id, operacion,
                                       payload, recibido_en)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(cursor) DO NOTHING
      ''',
      [
        delta.cursor,
        delta.entidad,
        delta.entidadId,
        delta.operacion,
        delta.payload == null ? null : jsonEncode(delta.payload),
        recibidoEn,
      ],
    );
  }

  // -------------------------------------------------------------------------
  // Catálogo
  // -------------------------------------------------------------------------

  bool _producto(Delta delta) {
    if (delta.operacion == 'delete') {
      // No se borra: un producto retirado del catálogo puede seguir
      // apareciendo en ventas ya hechas que aún no sincronizan. Se desactiva.
      _db.execute('UPDATE productos SET activo = 0 WHERE id = ?', [delta.entidadId]);
      return true;
    }
    final p = delta.payload!;
    _db.execute(
      '''
      INSERT INTO productos (id, sku, codigo_barras, nombre, categoria_id,
                             unidad_base, tasa_iva, activo)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        sku = excluded.sku,
        codigo_barras = excluded.codigo_barras,
        nombre = excluded.nombre,
        categoria_id = excluded.categoria_id,
        unidad_base = excluded.unidad_base,
        tasa_iva = excluded.tasa_iva,
        activo = excluded.activo
      ''',
      [
        p['id'],
        p['sku'],
        p['codigo_barras'],
        p['nombre'],
        p['categoria_id'],
        p['unidad_base'],
        _aNumero(p['tasa_iva']),
        _aBool(p['activo']),
      ],
    );
    return true;
  }

  bool _productoUnidad(Delta delta) {
    final p = delta.payload;
    if (p == null) return true;
    _db.execute(
      '''
      INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(producto_id, unidad_codigo) DO UPDATE SET
        factor = excluded.factor,
        es_default = excluded.es_default
      ''',
      [
        p['producto_id'],
        p['unidad_codigo'],
        _aNumero(p['factor']),
        _aBool(p['es_default']),
      ],
    );
    return true;
  }

  bool _precio(Delta delta) {
    final p = delta.payload;
    if (p == null) return true;
    _db.execute(
      '''
      INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio,
                           precio_minimo, version)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(lista_id, producto_id, unidad_codigo) DO UPDATE SET
        precio = excluded.precio,
        precio_minimo = excluded.precio_minimo,
        version = excluded.version
      ''',
      [
        p['lista_id'],
        p['producto_id'],
        p['unidad_codigo'],
        _aNumero(p['precio']),
        _aNumero(p['precio_minimo']),
        p['version'] ?? 1,
      ],
    );
    return true;
  }

  // -------------------------------------------------------------------------
  // Clientes
  // -------------------------------------------------------------------------

  bool _cliente(Delta delta) {
    if (delta.operacion == 'delete') {
      _db.execute('DELETE FROM clientes WHERE id = ?', [delta.entidadId]);
      return true;
    }
    final c = delta.payload!;
    final direccion = [c['calle'], c['numero'], c['colonia']]
        .whereType<String>()
        .where((s) => s.isNotEmpty)
        .join(' ');

    // `saldo_cache` NO se toca aquí: lo trae el delta de 'cartera'. Escribirlo
    // desde este payload lo pondría en cero, y el cálculo de crédito local le
    // diría al vendedor que un cliente endeudado tiene toda su línea libre.
    _db.execute(
      '''
      INSERT INTO clientes (id, codigo, nombre_comercial, telefono, direccion,
                            referencias, lat, lng, ubicacion_origen, secuencia,
                            lista_precios_id, permite_credito, limite_credito,
                            bloqueado, es_local, sincronizado)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
      ON CONFLICT(id) DO UPDATE SET
        codigo = excluded.codigo,
        nombre_comercial = excluded.nombre_comercial,
        telefono = excluded.telefono,
        direccion = excluded.direccion,
        referencias = excluded.referencias,
        lat = excluded.lat,
        lng = excluded.lng,
        ubicacion_origen = excluded.ubicacion_origen,
        secuencia = excluded.secuencia,
        lista_precios_id = excluded.lista_precios_id,
        permite_credito = excluded.permite_credito,
        limite_credito = excluded.limite_credito,
        bloqueado = excluded.bloqueado,
        -- El servidor ya lo conoce: deja de ser un alta local pendiente.
        es_local = 0,
        sincronizado = 1
      ''',
      [
        c['id'],
        c['codigo'],
        c['nombre_comercial'],
        c['telefono'],
        direccion.isEmpty ? null : direccion,
        c['referencias'],
        _aNumero(c['lat']),
        _aNumero(c['lng']),
        c['ubicacion_origen'],
        c['secuencia'],
        c['lista_precios_id'],
        _aBool(c['permite_credito']),
        _aNumero(c['limite_credito']),
        _aBool(c['bloqueado']),
      ],
    );
    return true;
  }

  /// El saldo y el límite, juntos.
  ///
  /// Solo actualiza clientes que el dispositivo ya tiene: si llegara la cartera
  /// antes que el cliente, insertar una fila a medias dejaría un renglón sin
  /// nombre en la lista de ruta. El cursor avanza igual y el cliente llegará en
  /// su propio delta.
  bool _cartera(Delta delta, String recibidoEn) {
    final c = delta.payload;
    if (c == null) return true;
    _db.execute(
      '''
      UPDATE clientes
         SET saldo_cache = ?,
             saldo_cache_en = ?,
             limite_credito = ?,
             permite_credito = ?,
             bloqueado = ?
       WHERE id = ?
      ''',
      [
        _aNumero(c['saldo']) ?? 0,
        recibidoEn,
        _aNumero(c['limite_credito']) ?? 0,
        _aBool(c['permite_credito']),
        _aBool(c['bloqueado']),
        delta.entidadId,
      ],
    );
    return true;
  }

  // -------------------------------------------------------------------------

  /// Los importes del servidor llegan como string (contracts/README.md §1).
  static num? _aNumero(Object? valor) => switch (valor) {
        null => null,
        final num n => n,
        final String s => num.tryParse(s),
        _ => null,
      };

  static int _aBool(Object? valor) => switch (valor) {
        true => 1,
        false || null => 0,
        final num n => n != 0 ? 1 : 0,
        _ => 0,
      };
}
