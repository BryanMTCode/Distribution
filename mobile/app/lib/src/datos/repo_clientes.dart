/// Clientes de la ruta, leídos de la base local.
///
/// Todo sale de SQLite: la pantalla funciona igual con señal o sin ella, y no
/// hay un estado "cargando del servidor" que deje al vendedor esperando frente
/// al cliente.
///
/// La operación es de contado (ADR 0002 §81): aquí ya no hay saldo, límite ni
/// bloqueo. Las columnas viejas siguen en la base local y nada las lee.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';

/// Un cliente tal como se muestra en la lista de ruta.
class ClienteEnRuta {
  const ClienteEnRuta({
    required this.id,
    required this.nombreComercial,
    this.codigo,
    this.telefono,
    this.direccion,
    this.referencias,
    this.secuencia,
    this.lat,
    this.lng,
    this.ubicacionOrigen,
    this.esLocal = false,
    this.plan = const [],
    this.tocaHoy = false,
    this.visitadoHoy = false,
  });

  final String id;
  final String nombreComercial;
  final String? codigo;
  final String? telefono;
  final String? direccion;
  final String? referencias;
  final int? secuencia;

  /// Dónde está el negocio: lo que usa la geocerca de la venta.
  final double? lat;
  final double? lng;

  /// 'gps' si se tomó parado en el negocio; 'manual' si se escribió o corrigió.
  final String? ubicacionOrigen;

  /// Alta hecha en este teléfono que aún no confirma el servidor.
  final bool esLocal;

  /// Qué días le toca visita, del plan que capturó la oficina.
  final List<DiaDeVisita> plan;

  /// Si hoy le toca, según su plan. Sin plan, nunca.
  final bool tocaHoy;

  /// Si hoy ya hay un papel suyo en este teléfono: venta, no-drop o devolución.
  /// Es la misma definición de «visita» que usa Efectividad en el servidor para
  /// contar lo que tocaba y nadie hizo.
  final bool visitadoHoy;

  bool get conUbicacion => lat != null && lng != null;
}

class RepoClientes {
  const RepoClientes(this._db);

  final Database _db;

  /// Clientes de la ruta, en orden de visita.
  List<ClienteEnRuta> deLaRuta({
    String? busqueda,
    int limite = 200,
    DateTime? hoy,
  }) {
    final filtro = (busqueda ?? '').trim();
    final tieneFiltro = filtro.isNotEmpty;
    // El día LOCAL: «hoy te tocan» es el lunes de la ruta, no el de Greenwich.
    final ahora = (hoy ?? DateTime.now()).toLocal();
    final fechaOperativa = diaOperativoDe(ahora);

    final filas = _db.select(
      '''
      SELECT c.id,
             c.codigo,
             c.nombre_comercial,
             c.telefono,
             c.direccion,
             c.referencias,
             c.secuencia,
             c.lat,
             c.lng,
             c.ubicacion_origen,
             c.es_local,
             c.plan_visita,
             (EXISTS (SELECT 1 FROM ventas v
                       WHERE v.cliente_id = c.id AND v.fecha_operativa = ?4)
              OR EXISTS (SELECT 1 FROM no_drops n
                          WHERE n.cliente_id = c.id AND n.fecha_operativa = ?4)
              OR EXISTS (SELECT 1 FROM mermas m
                          WHERE m.cliente_id = c.id AND m.fecha_operativa = ?4)
             ) AS visitado_hoy
        FROM clientes c
       WHERE c.activo = 1
         AND (?1 = 0 OR c.nombre_comercial LIKE ?2 OR c.codigo LIKE ?2
              OR c.telefono LIKE ?2)
       ORDER BY c.secuencia IS NULL, c.secuencia, c.nombre_comercial
       LIMIT ?3
      ''',
      [tieneFiltro ? 1 : 0, '%$filtro%', limite, fechaOperativa],
    );

    return filas.map((f) => _aCliente(f, ahora)).toList();
  }

  ClienteEnRuta? porId(String id) {
    final filas = deLaRuta(limite: 1000);
    for (final c in filas) {
      if (c.id == id) return c;
    }
    return null;
  }

  static ClienteEnRuta _aCliente(Row f, DateTime hoy) {
    final plan = leerPlanDeVisita(f['plan_visita'] as String?);
    return ClienteEnRuta(
      id: f['id'] as String,
      codigo: f['codigo'] as String?,
      nombreComercial: f['nombre_comercial'] as String,
      telefono: f['telefono'] as String?,
      direccion: f['direccion'] as String?,
      referencias: f['referencias'] as String?,
      secuencia: f['secuencia'] as int?,
      lat: (f['lat'] as num?)?.toDouble(),
      lng: (f['lng'] as num?)?.toDouble(),
      ubicacionOrigen: f['ubicacion_origen'] as String?,
      esLocal: (f['es_local'] as int) == 1,
      plan: plan,
      tocaHoy: tocaVisita(plan, hoy),
      visitadoHoy: (f['visitado_hoy'] as int? ?? 0) == 1,
    );
  }
}
