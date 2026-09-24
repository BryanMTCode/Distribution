/// Clientes de la ruta, leídos de la base local.
///
/// Todo sale de SQLite: la pantalla funciona igual con señal o sin ella, y no
/// hay un estado "cargando del servidor" que deje al vendedor esperando frente
/// al cliente.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';

/// Un cliente tal como se muestra en la lista de ruta.
class ClienteEnRuta {
  const ClienteEnRuta({
    required this.id,
    required this.nombreComercial,
    required this.credito,
    this.codigo,
    this.telefono,
    this.direccion,
    this.secuencia,
    this.saldoCacheEn,
    this.esLocal = false,
  });

  final String id;
  final String nombreComercial;
  final String? codigo;
  final String? telefono;
  final String? direccion;
  final int? secuencia;

  /// Estado de crédito ya compuesto con la cola local de este dispositivo.
  final EstadoCredito credito;

  /// Cuándo se sincronizó el saldo. Se muestra siempre: un saldo sin su
  /// antigüedad es un número en el que el vendedor confía más de lo debido.
  final String? saldoCacheEn;

  /// Alta hecha en este teléfono que aún no confirma el servidor.
  final bool esLocal;

  /// Lo que se pinta como distintivo en la lista.
  ResultadoCredito evaluar(Dinero total, {required bool aCredito}) =>
      evaluarVenta(credito, total, aCredito: aCredito);

  bool get creditoAgotado =>
      !credito.permiteCredito || credito.bloqueado || credito.disponible.esCero;
}

class RepoClientes {
  const RepoClientes(this._db);

  final Database _db;

  /// Clientes de la ruta, en orden de visita.
  ///
  /// El saldo efectivo se compone en SQL sumando lo que este dispositivo tiene
  /// sin sincronizar: ventas a crédito encoladas y cobros encolados. Sin eso,
  /// cinco ventas de la mañana pasarían todas el límite (ver
  /// `dsd_core/credito.dart`).
  List<ClienteEnRuta> deLaRuta({String? busqueda, int limite = 200}) {
    final filtro = (busqueda ?? '').trim();
    final tieneFiltro = filtro.isNotEmpty;

    final filas = _db.select(
      '''
      SELECT c.id,
             c.codigo,
             c.nombre_comercial,
             c.telefono,
             c.direccion,
             c.secuencia,
             c.permite_credito,
             c.bloqueado,
             c.limite_credito,
             c.saldo_cache,
             c.saldo_cache_en,
             c.es_local,
             COALESCE((
               SELECT SUM(v.total) FROM ventas v
                WHERE v.cliente_id = c.id
                  AND v.tipo = 'credito'
                  AND v.estado = 'confirmada'
                  AND v.sincronizada = 0
             ), 0) AS cargos_pendientes,
             COALESCE((
               SELECT SUM(k.importe) FROM cobros k
                WHERE k.cliente_id = c.id
                  AND k.estado = 'confirmado'
                  AND k.sincronizado = 0
             ), 0) AS abonos_pendientes
        FROM clientes c
       WHERE (?1 = 0 OR c.nombre_comercial LIKE ?2 OR c.codigo LIKE ?2
              OR c.telefono LIKE ?2)
       ORDER BY c.secuencia IS NULL, c.secuencia, c.nombre_comercial
       LIMIT ?3
      ''',
      [tieneFiltro ? 1 : 0, '%$filtro%', limite],
    );

    return filas.map(_aCliente).toList();
  }

  ClienteEnRuta? porId(String id) {
    final filas = deLaRuta(limite: 1000);
    for (final c in filas) {
      if (c.id == id) return c;
    }
    return null;
  }

  static ClienteEnRuta _aCliente(Row f) {
    // Los importes viven en la base local como REAL por compatibilidad con el
    // esquema, y se convierten a centavos exactos al entrar al dominio. La
    // aritmética de dinero nunca ocurre en double.
    Dinero desdeBase(Object? valor) =>
        Dinero.deTexto(((valor as num?) ?? 0).toDouble().toStringAsFixed(2));

    return ClienteEnRuta(
      id: f['id'] as String,
      codigo: f['codigo'] as String?,
      nombreComercial: f['nombre_comercial'] as String,
      telefono: f['telefono'] as String?,
      direccion: f['direccion'] as String?,
      secuencia: f['secuencia'] as int?,
      saldoCacheEn: f['saldo_cache_en'] as String?,
      esLocal: (f['es_local'] as int) == 1,
      credito: EstadoCredito(
        limite: desdeBase(f['limite_credito']),
        saldoConfirmado: desdeBase(f['saldo_cache']),
        permiteCredito: (f['permite_credito'] as int) == 1,
        bloqueado: (f['bloqueado'] as int) == 1,
        cargosPendientes: desdeBase(f['cargos_pendientes']),
        abonosPendientes: desdeBase(f['abonos_pendientes']),
      ),
    );
  }
}
