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
        'carga' => _carga(delta, recibidoEn),
        'venta' => _venta(delta, recibidoEn),
        'ajuste_camion' => _ajusteCamion(delta, recibidoEn),
        'motivo_merma' => _motivoMerma(delta),
        'motivo_no_drop' => _motivoNoDrop(delta),
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

    // Un precio QUITADO se borra, y el payload dice cuál.
    //
    // El delta de precio se acota por `producto_id`, no por la llave del renglón:
    // un producto tiene un precio por lista y por presentación. Antes el servidor
    // mandaba el DELETE con el payload en NULL y el aplicador salía por arriba sin
    // hacer nada, así que **el vendedor seguía ofreciendo una presentación que la
    // oficina había retirado**, al precio que tenía. Desde la migración 0033 el
    // payload del borrado trae los tres campos que identifican el renglón.
    if (delta.operacion == 'delete') {
      _db.execute(
        'DELETE FROM precios '
        ' WHERE lista_id = ? AND producto_id = ? AND unidad_codigo = ?',
        [p['lista_id'], p['producto_id'], p['unidad_codigo']],
      );
      return true;
    }
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
  // Los catálogos de motivos
  // -------------------------------------------------------------------------

  /// Los motivos de merma y devolución.
  ///
  /// Sin esta tabla llena, la pantalla de merma abre con la lista vacía y el
  /// vendedor no puede registrar nada — y el motivo es de catálogo cerrado justo
  /// para que no pueda escribir texto libre. El servidor nunca los publicaba
  /// (migración 0017); el defecto se nota en la calle, cuando una caja se rompe.
  ///
  /// La llave primaria es el `codigo`, no el `entidad_id` del delta: ése es un md5
  /// del código, estable, que existe solo porque `change_log.entidad_id` es uuid.
  bool _motivoMerma(Delta delta) {
    final m = delta.payload;
    if (m == null) return true;
    _db.execute(
      '''
      INSERT INTO motivos_merma (codigo, nombre, afecta_vendedor, activo)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(codigo) DO UPDATE SET
        nombre = excluded.nombre,
        afecta_vendedor = excluded.afecta_vendedor,
        activo = excluded.activo
      ''',
      [
        m['codigo'],
        m['nombre'],
        _aBool(m['afecta_vendedor']),
        // Sin esto, un motivo que la oficina retiró seguiría apareciendo en la
        // pantalla del vendedor: para él la desactivación nunca habría pasado.
        m.containsKey('activo') ? _aBool(m['activo']) : 1,
      ],
    );
    return true;
  }

  /// Los motivos de visita sin venta.
  ///
  /// `orden` viaja porque en la calle, con el cliente esperando, un catálogo
  /// alfabético obliga a leer diez opciones para encontrar "cerrado".
  bool _motivoNoDrop(Delta delta) {
    final m = delta.payload;
    if (m == null) return true;
    _db.execute(
      '''
      INSERT INTO motivos_no_drop (codigo, nombre, categoria, requiere_nota,
                                   orden, activo)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(codigo) DO UPDATE SET
        nombre = excluded.nombre,
        categoria = excluded.categoria,
        requiere_nota = excluded.requiere_nota,
        orden = excluded.orden,
        activo = excluded.activo
      ''',
      [
        m['codigo'],
        m['nombre'],
        m['categoria'],
        _aBool(m['requiere_nota']),
        _aNumero(m['orden']) ?? 0,
        m.containsKey('activo') ? _aBool(m['activo']) : 1,
      ],
    );
    return true;
  }

  // -------------------------------------------------------------------------
  // La carga del camión
  // -------------------------------------------------------------------------

  /// El inventario que el vendedor trae encima.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// EL CAMIÓN ES UN ALMACÉN RODANTE: LA CARGA SE SUMA, NO REEMPLAZA
  /// ───────────────────────────────────────────────────────────────────────
  /// Decisión de la dirección, octubre 2026: la mercancía que no se vende se
  /// queda a dormir en el camión y se acumula con la carga del día siguiente.
  ///
  /// Antes este método hacía lo contrario, y lo decía con estas palabras: «la
  /// carga confirmada es el inventario completo con el que arranca el día». Con
  /// mercancía que duerme arriba, eso le borraba al vendedor lo que traía, y el
  /// cierre del servidor se lo cobraba como faltante.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// SUMAR OBLIGA A SABER SI YA SE SUMÓ
  /// ───────────────────────────────────────────────────────────────────────
  /// Reemplazar era idempotente por naturaleza: escribir dos veces el mismo
  /// número da el mismo número. Sumar no lo es. Un `pull` se puede repetir tras
  /// un corte de red, y entonces este mismo delta llega dos veces; sumarlo dos
  /// veces le regalaría al camión una carga completa. El vendedor la ofrecería,
  /// no la tendría, y el descuadre saldría en la liquidación sin explicación.
  ///
  /// De eso se encarga `cargas_aplicadas`: una carga se suma UNA vez, y la
  /// segunda llegada solo refresca los marcadores del día.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// EL CIERRE TRAE UN AJUSTE, NO UNA ORDEN DE VACIAR
  /// ───────────────────────────────────────────────────────────────────────
  /// Cuando la oficina liquida, el delta llega con `estado: 'liquidada'` y con
  /// los `ajustes` del conteo físico: la diferencia, con signo, entre lo que se
  /// contó arriba del camión y lo que el sistema creía. El teléfono la SUMA.
  ///
  /// Es una diferencia y no un conteo por una razón de calendario: la oficina
  /// liquida lo de ayer a media mañana, con la carga de hoy ya encima y con
  /// ventas hechas. Un conteo de ayer aplicado como «el camión tiene esto»
  /// borraría la carga de hoy y las ventas de la mañana. Una diferencia sigue
  /// siendo correcta cuando llega tarde.
  bool _carga(Delta delta, String recibidoEn) {
    if (delta.operacion == 'delete') {
      // El payload de un borrado viene vacío, así que no hay detalle que restar.
      _soltarCargaActiva(delta.entidadId);
      return true;
    }

    final c = delta.payload;
    if (c == null) return true;

    final estado = c['estado'] as String?;

    // El cierre del día: se aplica el ajuste y la carga deja de ser la activa.
    // La mercancía NO se borra: sigue arriba del camión.
    if (estado == 'liquidada') {
      _aplicarAjusteDelCierre(delta.entidadId, c['ajustes'], recibidoEn);
      _soltarCargaActiva(delta.entidadId);
      return true;
    }

    // Cancelada: la carga nunca debió salir, así que se deshace lo que subió.
    if (estado == 'cancelada') {
      _deshacerCarga(delta.entidadId, c['detalle']);
      return true;
    }

    // El servidor no publica borradores (migración 0015), pero si algún día lo
    // hiciera, el teléfono no debe mostrar mercancía que la bodega no entregó.
    if (estado != 'confirmada' && estado != 'en_ruta') return true;

    final detalle = (c['detalle'] as List?) ?? const [];

    // Una carga confirmada SIN renglones no cambia el inventario.
    //
    // El panel no deja confirmar una carga vacía, así que esto solo puede llegar
    // de un script corriendo contra la base —alguien que inserta la carga ya en
    // 'confirmada' y le pone el detalle después—. Lo que NO se hace es marcarla
    // como aplicada: si se marcara, el delta que llegara después con los
    // renglones completos no entraría nunca.
    if (detalle.isEmpty) {
      _marcarCargaActiva(delta.entidadId, c['fecha_operativa']);
      return true;
    }

    if (!_cargaYaAplicada(delta.entidadId)) {
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
          "INSERT INTO existencias_camion (producto_id, cant_cargada, "
          "                                cant_actual, carga_id) "
          "VALUES (?1, ?2, ?2, ?3) "
          "ON CONFLICT(producto_id) DO UPDATE SET "
          // El saldo SE SUMA: lo de antes sigue arriba del camión.
          "  cant_actual  = existencias_camion.cant_actual + excluded.cant_actual, "
          // Y `cant_cargada` es lo de ESTA carga, no un acumulado: dice cuánto
          // entregó la bodega la última vez, que es lo que se compara contra el
          // papel de la carga.
          "  cant_cargada = excluded.cant_cargada, "
          "  carga_id     = excluded.carga_id",
          [producto, cantidad.milesimos / 1000, delta.entidadId],
        );
      }

      _db.execute(
        'INSERT INTO cargas_aplicadas (carga_id, aplicada_en) VALUES (?, ?)',
        [delta.entidadId, recibidoEn],
      );
    }

    _marcarCargaActiva(delta.entidadId, c['fecha_operativa']);
    return true;
  }

  bool _cargaYaAplicada(String cargaId) => _db
      .select('SELECT 1 FROM cargas_aplicadas WHERE carga_id = ?', [cargaId])
      .isNotEmpty;

  /// La carga activa: de aquí la lee el carrito para estampar `carga_id` en cada
  /// venta. Sin ella las ventas del día no se pueden amarrar a la carga y la
  /// liquidación no tendría contra qué cuadrar.
  void _marcarCargaActiva(String cargaId, Object? fechaOperativa) {
    _db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('carga_id_activa', ?) "
      'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
      [cargaId],
    );
    if (fechaOperativa != null) {
      _db.execute(
        "INSERT INTO sync_estado (clave, valor) VALUES ('fecha_operativa', ?) "
        'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
        [fechaOperativa],
      );
    }
  }

  /// El ajuste que la oficina escribió al comparar el conteo contra el sistema.
  ///
  /// Negativo es faltante —se le cobró al vendedor, y sale del camión—; positivo
  /// es sobrante y entra. Se aplica UNA vez: aplicarlo dos dejaría el teléfono
  /// con menos mercancía de la que el vendedor trae encima.
  void _aplicarAjusteDelCierre(
    String cargaId,
    Object? ajustes,
    String recibidoEn,
  ) {
    final ya = _db.select(
      'SELECT ajuste_aplicado_en FROM cargas_aplicadas WHERE carga_id = ?',
      [cargaId],
    );
    if (ya.isNotEmpty && ya.single['ajuste_aplicado_en'] != null) return;

    if (ajustes is List) {
      for (final fila in ajustes) {
        if (fila is! Map<String, Object?>) continue;
        final producto = fila['producto_id'] as String?;
        if (producto == null) continue;
        final cantidad = Cantidad.deTexto(_aTextoCantidad(fila['cantidad']));
        if (cantidad.esCero) continue;

        // Sin `INSERT`: un ajuste sobre un producto que el camión no trae no
        // crea el renglón. Si no lo trae, el ajuste ya está reflejado en eso.
        _db.execute(
          'UPDATE existencias_camion SET cant_actual = cant_actual + ? '
          ' WHERE producto_id = ?',
          [cantidad.milesimos / 1000, producto],
        );
      }
    }

    // Se marca aunque la carga no estuviera registrada —puede venir de antes de
    // esta versión—: lo que importa es no aplicar el mismo ajuste dos veces.
    _db.execute(
      'INSERT INTO cargas_aplicadas (carga_id, aplicada_en, ajuste_aplicado_en) '
      'VALUES (?1, ?2, ?2) '
      'ON CONFLICT(carga_id) DO UPDATE SET ajuste_aplicado_en = ?2',
      [cargaId, recibidoEn],
    );
  }

  /// Deshace lo que una carga había subido al camión.
  ///
  /// Resta su detalle en vez de borrar los renglones: lo que había ANTES de esa
  /// carga sigue arriba del camión, y borrar el renglón se llevaría también el
  /// sobrante de los días anteriores.
  ///
  /// Si la carga nunca se aplicó no hay nada que deshacer, y eso es lo correcto:
  /// cancelar un borrador no debe mover inventario.
  void _deshacerCarga(String cargaId, Object? detalle) {
    if (_cargaYaAplicada(cargaId) && detalle is List) {
      for (final fila in detalle) {
        if (fila is! Map<String, Object?>) continue;
        final producto = fila['producto_id'] as String?;
        if (producto == null) continue;
        final cantidad = Cantidad.deTexto(_aTextoCantidad(fila['cantidad']));
        _db.execute(
          'UPDATE existencias_camion SET cant_actual = cant_actual - ? '
          ' WHERE producto_id = ?',
          [cantidad.milesimos / 1000, producto],
        );
      }
      _db.execute('DELETE FROM cargas_aplicadas WHERE carga_id = ?', [cargaId]);
    }
    _soltarCargaActiva(cargaId);
  }

  /// Si la carga que terminó era la activa, deja de serlo. Una venta sin carga es
  /// mejor que una venta amarrada a una carga ya cerrada.
  void _soltarCargaActiva(String cargaId) {
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
  // El ajuste que la oficina hizo al camión
  // -------------------------------------------------------------------------

  /// Una corrección de la oficina al inventario del camión.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// LLEGA UN DELTA FIRMADO, Y POR ESO HAY QUE RECORDAR CUÁLES SE APLICARON
  /// ───────────────────────────────────────────────────────────────────────
  /// El servidor podría mandar el saldo resultante —«el camión tiene 12»— y
  /// aplicarlo dos veces sería inofensivo. No lo hace: el vendedor puede estar
  /// vendiendo mientras la oficina corrige, y un saldo de hace cinco minutos
  /// aplicado ahora borraría las ventas de esos cinco minutos.
  ///
  /// El precio de mandar el delta es que sumarlo dos veces está mal, y un `pull`
  /// se repite cuando la red se corta a media tanda. De eso se encarga
  /// `ajustes_camion_aplicados`: un ajuste se suma UNA vez.
  ///
  /// Es el mismo trato que con la carga y con el ajuste del cierre. La regla
  /// general del sistema: **lo que viaja como diferencia se marca; lo que viaja
  /// como estado se compara.**
  bool _ajusteCamion(Delta delta, String recibidoEn) {
    final a = delta.payload;
    if (a == null) return true;

    final ya = _db.select(
      'SELECT 1 FROM ajustes_camion_aplicados WHERE ajuste_id = ?',
      [delta.entidadId],
    );
    if (ya.isNotEmpty) return true;

    final producto = a['producto_id'] as String?;
    if (producto == null) return true;
    final cantidad = Cantidad.deTexto(_aTextoCantidad(a['delta']));

    // Sin `INSERT`: un ajuste sobre un producto que el camión no trae no crea el
    // renglón. Si no lo trae, la carga no lo subió, y un renglón nuevo le
    // mostraría al vendedor mercancía que no tiene.
    _db.execute(
      'UPDATE existencias_camion SET cant_actual = cant_actual + ? '
      ' WHERE producto_id = ?',
      [cantidad.milesimos / 1000, producto],
    );

    // La marca se escribe aunque el renglón no existiera: lo que importa es no
    // volver a aplicar este ajuste si el delta llega otra vez.
    _db.execute(
      'INSERT INTO ajustes_camion_aplicados (ajuste_id, folio, nota, aplicado_en) '
      'VALUES (?, ?, ?, ?)',
      [
        delta.entidadId,
        a['folio'] as String?,
        a['nota'] as String?,
        recibidoEn,
      ],
    );
    return true;
  }

  // -------------------------------------------------------------------------
  // La venta que la oficina cambió
  // -------------------------------------------------------------------------

  /// Una venta que la OFICINA canceló o corrigió.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// NO LLEGA LA DIFERENCIA: LLEGAN LAS PARTIDAS, Y SE COMPARAN
  /// ───────────────────────────────────────────────────────────────────────
  /// El servidor podría mandar «devuelve 432 piezas al camión» y sería un payload
  /// más chico. No lo hace, y la razón es la de siempre con los deltas: un `pull`
  /// se repite cuando la red se corta a media tanda, y aplicar dos veces «devuelve
  /// 432» le regala al camión 432 piezas que no existen.
  ///
  /// Así que viajan las partidas como quedaron, y el teléfono devuelve al camión la
  /// DIFERENCIA contra lo que él tiene guardado. Aplicarlo dos veces da cero la
  /// segunda vez: la comparación es idempotente por construcción, sin tabla de
  /// marcas y sin que nadie tenga que acordarse.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// UNA VENTA QUE ESTE TELÉFONO NO TIENE NO SE INVENTA
  /// ───────────────────────────────────────────────────────────────────────
  /// Pasa después de reinstalar la app: el servidor tiene la venta y el teléfono
  /// no. Entonces no hay con qué comparar, y tocar el camión sería adivinar —el
  /// saldo que este teléfono trae ya viene del servidor, con esa venta dentro—. Se
  /// ignora, que es lo correcto: lo que el vendedor necesita de una venta vieja lo
  /// ve en el panel, no en su «Mi día» de hoy.
  bool _venta(Delta delta, String recibidoEn) {
    final v = delta.payload;
    if (v == null) return true;

    final local = _db.select(
      'SELECT estado, total FROM ventas WHERE id = ?',
      [delta.entidadId],
    );
    if (local.isEmpty) return true;

    final cancelada = (v['estado'] as String?) == 'cancelada';

    // Lo que el teléfono tiene por producto, en unidad base.
    final mias = <String, int>{};
    for (final f in _db.select(
      'SELECT producto_id, cantidad_base FROM venta_partidas WHERE venta_id = ?',
      [delta.entidadId],
    )) {
      mias.update(
        f['producto_id'] as String,
        (previo) =>
            previo + Cantidad.deBase((f['cantidad_base'] as num).toDouble()).milesimos,
        ifAbsent: () =>
            Cantidad.deBase((f['cantidad_base'] as num).toDouble()).milesimos,
      );
    }

    // Lo que el servidor dice que quedó. Una venta cancelada no tiene nada: toda
    // su mercancía vuelve.
    final suyas = <String, int>{};
    final partidas = (v['partidas'] as List?) ?? const [];
    if (!cancelada) {
      for (final fila in partidas) {
        if (fila is! Map<String, Object?>) continue;
        final producto = fila['producto_id'] as String?;
        if (producto == null) continue;
        final base = Cantidad.deTexto(_aTextoCantidad(fila['cantidad_base']));
        suyas.update(
          producto,
          (previo) => previo + base.milesimos,
          ifAbsent: () => base.milesimos,
        );
      }
    }

    // La diferencia vuelve al camión. Sin `INSERT`: un producto que el camión no
    // trae no se crea aquí — si no lo trae, es porque la carga no lo subió, y un
    // renglón nuevo le mostraría al vendedor mercancía que no tiene.
    for (final producto in {...mias.keys, ...suyas.keys}) {
      final vuelve = (mias[producto] ?? 0) - (suyas[producto] ?? 0);
      if (vuelve == 0) continue;
      _db.execute(
        'UPDATE existencias_camion SET cant_actual = cant_actual + ? '
        ' WHERE producto_id = ?',
        [vuelve / 1000, producto],
      );
    }

    // Y la venta queda como el servidor la tiene. Las partidas se reescriben
    // completas: una cancelada se queda sin ninguna, y una corregida puede haber
    // perdido un renglón entero.
    _db.execute('DELETE FROM venta_partidas WHERE venta_id = ?', [delta.entidadId]);
    for (final fila in partidas) {
      if (fila is! Map<String, Object?>) continue;
      final producto = fila['producto_id'] as String?;
      if (producto == null) continue;
      _db.execute(
        'INSERT INTO venta_partidas (id, venta_id, linea, producto_id, '
        '                            unidad_codigo, factor_unidad, cantidad, '
        '                            cantidad_base, precio_unitario, tasa_iva, '
        '                            importe) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
          fila['id'] as String? ?? '${delta.entidadId}-${fila['linea']}',
          delta.entidadId,
          _aNumero(fila['linea']) ?? 1,
          producto,
          fila['unidad_codigo'] as String? ?? 'PZA',
          _aNumero(fila['factor_unidad']) ?? 1,
          _aNumero(fila['cantidad']) ?? 0,
          _aNumero(fila['cantidad_base']) ?? 0,
          _aNumero(fila['precio_unitario']) ?? 0,
          _aNumero(fila['tasa_iva']) ?? 0,
          _aNumero(fila['importe']) ?? 0,
        ],
      );
    }

    // La nota es para que el vendedor LEA por qué su venta cambió. Que cambie sin
    // decirle por qué es la forma más rápida de que deje de confiar en el sistema.
    final motivo = cancelada
        ? (v['cancelacion_motivo'] as String?)
        : (v['correccion_motivo'] as String?);

    _db.execute(
      'UPDATE ventas SET estado = ?, subtotal = ?, descuento = ?, impuestos = ?, '
      '                 total = ?, nota_oficina = ? '
      ' WHERE id = ?',
      [
        v['estado'] as String? ?? 'confirmada',
        _aNumero(v['subtotal']) ?? 0,
        _aNumero(v['descuento']) ?? 0,
        _aNumero(v['impuestos']) ?? 0,
        _aNumero(v['total']) ?? 0,
        motivo,
        delta.entidadId,
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
