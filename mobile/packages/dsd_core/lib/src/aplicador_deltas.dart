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

import 'precio.dart';
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
        'lista_precios' => _listaPrecios(delta),
        'carga' => _carga(delta),
        // Las promociones todavía no se aplican: se aceptan para no llenar
        // `deltas_desconocidos` con algo que sí sabemos que viene.
        'promocion' => true,
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

  /// Las listas de precios.
  ///
  /// El dispositivo las guarda por un caso muy concreto: el vendedor da de alta
  /// una tienda en la calle y le quiere vender **en ese momento**. Ese cliente
  /// nace sin lista —la asigna el servidor al confirmarlo—, así que se le cotiza
  /// con la lista por omisión. Sin esta tabla no habría con qué.
  bool _listaPrecios(Delta delta) {
    if (delta.operacion == 'delete') {
      // No se borra: los precios ya sincronizados siguen apuntando a ella, y un
      // cliente todavía puede traerla asignada. Se marca inactiva.
      _db.execute(
        'UPDATE listas_precios SET activo = 0 WHERE id = ?',
        [delta.entidadId],
      );
      return true;
    }
    final l = delta.payload!;
    _db.execute(
      '''
      INSERT INTO listas_precios (id, codigo, nombre, es_default, activo)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        codigo = excluded.codigo,
        nombre = excluded.nombre,
        es_default = excluded.es_default,
        activo = excluded.activo
      ''',
      [
        delta.entidadId,
        l['codigo'],
        l['nombre'],
        (l['es_default'] == true) ? 1 : 0,
        (l['activo'] == false) ? 0 : 1,
      ],
    );
    return true;
  }

  // -------------------------------------------------------------------------
  // La carga del camión
  // -------------------------------------------------------------------------

  /// El inventario con el que el vendedor sale a la calle.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// LA CARGA CONFIRMADA ES EL SNAPSHOT BASE DEL DÍA
  /// ───────────────────────────────────────────────────────────────────────
  /// Es el único delta que trae su detalle dentro del mismo payload, y no es un
  /// capricho: el teléfono necesita la carga **completa o nada**. Con un delta
  /// por renglón, una tanda cortada a la mitad dejaría el camión con cinco de
  /// los doce productos que trae, y el vendedor descubriría el faltante frente
  /// al cliente.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// LO QUE NUNCA DEBE PASAR: QUE REAPLICAR EL DELTA REVIVA LO VENDIDO
  /// ───────────────────────────────────────────────────────────────────────
  /// Un `pull` se puede repetir tras un corte de red, y entonces este mismo
  /// delta llega dos veces. Si la segunda vez volviera a escribir
  /// `cant_actual = cant_cargada`, el camión recuperaría en la base la
  /// mercancía que ya salió físicamente, y el vendedor podría venderla otra
  /// vez. El descuadre aparecería en la liquidación como un faltante que nadie
  /// sabría explicar.
  ///
  /// Por eso el `ON CONFLICT` lleva un `WHERE`: un renglón que **ya pertenece a
  /// esta carga** no se toca. Solo se sobrescribe el que viene de otra carga —el
  /// sobrante de ayer— o el que no tenía ninguna.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// UNA CARGA QUE YA TERMINÓ NO BORRA LA DE HOY
  /// ───────────────────────────────────────────────────────────────────────
  /// Cuando la oficina liquida o cancela una carga, el servidor emite otro delta
  /// de esa misma carga con el estado nuevo. Ese delta puede llegar **después**
  /// de la carga de hoy —la oficina liquida lo de ayer a media mañana—, así que
  /// no puede tratarse como "éste es el inventario vigente": borraría el de hoy
  /// y repondría el de ayer.
  ///
  /// La regla: un estado terminal borra **solo sus propios renglones**. Si la de
  /// hoy ya los reemplazó, no borra nada, que es exactamente lo correcto.
  bool _carga(Delta delta) {
    if (delta.operacion == 'delete') {
      _borrarCarga(delta.entidadId);
      return true;
    }

    final c = delta.payload;
    if (c == null) return true;

    final estado = c['estado'] as String?;

    // Terminales: la carga se acabó. Solo lo suyo.
    if (estado == 'liquidada' || estado == 'cancelada') {
      _borrarCarga(delta.entidadId);
      return true;
    }

    // El servidor no publica borradores (migración 0015), pero si algún día lo
    // hiciera, el teléfono no debe mostrar mercancía que la bodega no entregó.
    if (estado != 'confirmada' && estado != 'en_ruta') return true;

    final detalle = (c['detalle'] as List?) ?? const [];

    // Una carga confirmada SIN renglones no vacía el camión.
    //
    // El panel no deja confirmar una carga vacía, así que esto solo puede llegar
    // de un script corriendo contra la base —alguien que inserta la carga ya en
    // 'confirmada' y le pone el detalle después—. El delta saldría con
    // `detalle: []`, y tratarlo como el inventario del día le dejaría el camión
    // vacío al vendedor a media ruta.
    //
    // Ignorarlo es seguro porque el caso legítimo no existe: una carga sin
    // renglones no es una carga. Si el detalle llega después, el UPDATE que lo
    // acompañe publica otro delta con los renglones completos.
    if (detalle.isEmpty) return true;

    // Lo que no es de esta carga es el sobrante de un día anterior. Se va: la
    // carga confirmada es el inventario completo con el que arranca el día.
    _db.execute(
      'DELETE FROM existencias_camion WHERE carga_id IS NOT ?',
      [delta.entidadId],
    );

    for (final fila in detalle) {
      final r = fila as Map<String, Object?>;
      final producto = r['producto_id'] as String?;
      if (producto == null) continue;

      // La cantidad llega como string de tres decimales
      // (contracts/README.md §1.4). Pasa por `Cantidad` para que ningún
      // `double` la toque: de ahí salen las milésimas enteras y de vuelta al
      // REAL de SQLite, que es lo que el resto del teléfono ya lee.
      final cantidad = Cantidad.deTexto(_aTextoCantidad(r['cantidad']));

      _db.execute(
        '''
        INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual,
                                        carga_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(producto_id) DO UPDATE SET
          cant_cargada = excluded.cant_cargada,
          cant_actual  = excluded.cant_actual,
          carga_id     = excluded.carga_id
        WHERE existencias_camion.carga_id IS NOT excluded.carga_id
        ''',
        [
          producto,
          cantidad.milesimos / 1000,
          cantidad.milesimos / 1000,
          delta.entidadId,
        ],
      );
    }

    // La carga activa: de aquí la lee el carrito para estampar `carga_id` en
    // cada venta. Sin ella las ventas del día no se pueden amarrar a la carga y
    // la liquidación no tendría contra qué cuadrar.
    _db.execute(
      '''
      INSERT INTO sync_estado (clave, valor) VALUES ('carga_id_activa', ?)
      ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
      ''',
      [delta.entidadId],
    );
    if (c['fecha_operativa'] != null) {
      _db.execute(
        '''
        INSERT INTO sync_estado (clave, valor) VALUES ('fecha_operativa', ?)
        ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
        ''',
        [c['fecha_operativa']],
      );
    }
    return true;
  }

  void _borrarCarga(String cargaId) {
    _db.execute('DELETE FROM existencias_camion WHERE carga_id = ?', [cargaId]);
    // Si la que terminó era la activa, deja de serlo. Una venta sin carga es
    // mejor que una venta amarrada a una carga ya liquidada.
    _db.execute(
      "DELETE FROM sync_estado WHERE clave = 'carga_id_activa' AND valor = ?",
      [cargaId],
    );
  }

  /// La cantidad tal como la manda el servidor, lista para `Cantidad.deTexto`.
  ///
  /// El contrato dice string de tres decimales, y así la emite el disparador.
  /// Se acepta también un número por si un servidor viejo lo manda crudo: se
  /// convierte con `toStringAsFixed(3)`, que es el único cruce permitido entre
  /// un `double` y el dominio.
  static String _aTextoCantidad(Object? valor) => switch (valor) {
        final String s => s,
        final num n => n.toStringAsFixed(3),
        _ => '0.000',
      };

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
