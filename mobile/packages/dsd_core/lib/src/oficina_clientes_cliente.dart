/// Los clientes para la oficina: el cliente de `/v1/oficina/clientes`.
///
/// No es la lista de ruta que el vendedor baja para vender sin señal: es la vista
/// de la oficina sobre TODOS los clientes —dónde están, qué compran y cómo pagan—.
/// Todo es de contado (ADR 0002 §81): aquí no hay saldo ni crédito. Necesita red,
/// como el tablero.
library;

import 'dart:convert';

import 'cargas_cliente.dart' show CargaRechazada;
import 'dinero.dart';
import 'forma_de_pago.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';
import 'ubicacion.dart';
import 'vendedores_cliente.dart' show ServidorSinEstaFuncion, SinPermisoDeVendedores;

Dinero _d(Object? v) => Dinero.deTexto(v! as String);

double? _coordenada(Object? v) => v == null ? null : double.parse(v.toString());

class ClienteDeOficina {
  const ClienteDeOficina({
    required this.id,
    required this.codigo,
    required this.nombre,
    required this.ruta,
    required this.estatus,
    required this.telefono,
    required this.conUbicacion,
    required this.ultimaCompra,
    this.rutaId,
    this.rutaCodigo,
    this.dias = const [],
    this.orden,
  });

  factory ClienteDeOficina.deJson(Map<String, Object?> j) => ClienteDeOficina(
        id: j['id']! as String,
        codigo: j['codigo'] as String?,
        nombre: j['nombre']! as String,
        ruta: j['ruta'] as String?,
        estatus: j['estatus']! as String,
        telefono: j['telefono'] as String?,
        conUbicacion: j['con_ubicacion']! as bool,
        ultimaCompra: j['ultima_compra'] as String?,
        rutaId: j['ruta_id'] as String?,
        rutaCodigo: j['ruta_codigo'] as String?,
        dias: [for (final d in (j['dias'] ?? const <Object?>[]) as List) (d! as num).toInt()],
        orden: (j['orden'] as num?)?.toInt(),
      );

  final String id;
  final String? codigo;
  final String nombre;
  final String? ruta;
  final String estatus;
  final String? telefono;

  /// Sin coordenadas el vendedor no tiene geocerca y el mapa no lo dibuja.
  final bool conUbicacion;

  /// `YYYY-MM-DD`, o nulo si nunca ha comprado.
  final String? ultimaCompra;

  // Para agruparlos por ruta y día (ADR 0002 §122). Un servidor anterior no los
  // manda.
  final String? rutaId;
  final String? rutaCodigo;

  /// Los días de visita, 0–6 como `extract(dow)` (1 = lunes); vacía, sin día.
  final List<int> dias;

  /// Su lugar en el recorrido de la ruta.
  final int? orden;
}

/// Un día de la ruta con cuántas tiendas le tocan; `dia` nulo, las que no
/// tienen día de visita.
class DiaConTiendas {
  const DiaConTiendas({required this.dia, required this.tiendas});

  factory DiaConTiendas.deJson(Map<String, Object?> j) => DiaConTiendas(
        dia: (j['dia'] as num?)?.toInt(),
        tiendas: (j['tiendas']! as num).toInt(),
      );

  final int? dia;
  final int tiendas;
}

/// Una ruta con sus tiendas de cada día: el resumen con el que abre la lista.
class RutaConTiendas {
  const RutaConTiendas({
    required this.clave,
    required this.codigo,
    required this.nombre,
    required this.vendedor,
    required this.total,
    required this.porDia,
  });

  factory RutaConTiendas.deJson(Map<String, Object?> j) => RutaConTiendas(
        clave: j['clave']! as String,
        codigo: j['codigo'] as String?,
        nombre: j['nombre']! as String,
        vendedor: j['vendedor'] as String?,
        total: (j['total']! as num).toInt(),
        porDia: [
          for (final d in (j['por_dia'] ?? const <Object?>[]) as List)
            DiaConTiendas.deJson((d as Map).cast()),
        ],
      );

  /// El id de la ruta, o «sin» para las tiendas sin ruta: lo que va en `ruta`.
  final String clave;
  final String? codigo;
  final String nombre;
  final String? vendedor;
  final int total;
  final List<DiaConTiendas> porDia;
}

class ListaDeClientesDeOficina {
  const ListaDeClientesDeOficina({
    required this.filtro,
    required this.clientes,
    required this.recortado,
    required this.conteos,
    this.ruta = '',
    this.dia = '',
    this.rutas = const [],
    this.listado = true,
  });

  factory ListaDeClientesDeOficina.deJson(Map<String, Object?> j) =>
      ListaDeClientesDeOficina(
        filtro: j['filtro']! as String,
        clientes: [
          for (final c in j['clientes']! as List)
            ClienteDeOficina.deJson((c as Map).cast()),
        ],
        recortado: j['recortado']! as bool,
        conteos: {
          for (final e in (j['conteos']! as Map).entries)
            e.key as String: (e.value as num).toInt(),
        },
        ruta: (j['ruta'] as String?) ?? '',
        dia: (j['dia'] as String?) ?? '',
        rutas: [
          for (final r in (j['rutas'] ?? const <Object?>[]) as List)
            RutaConTiendas.deJson((r as Map).cast()),
        ],
        listado: (j['listado'] as bool?) ?? true,
      );

  final String filtro;
  final List<ClienteDeOficina> clientes;
  final bool recortado;

  /// Cuántos hay en cada filtro: todos, prospectos, sin_ubicacion.
  final Map<String, int> conteos;

  /// La ruta y el día elegidos (`''` ninguno; «sin» las que no tienen).
  final String ruta;
  final String dia;

  /// Las rutas con sus tiendas de cada día. Vacía con un servidor anterior.
  final List<RutaConTiendas> rutas;

  /// Si se listaron tiendas o solo el resumen de rutas (sin ruta ni día).
  final bool listado;
}

class VentaDelCliente {
  const VentaDelCliente({
    required this.id,
    required this.folio,
    required this.fecha,
    required this.tipo,
    required this.formaDePago,
    required this.estado,
    required this.total,
    required this.vendedor,
  });

  factory VentaDelCliente.deJson(Map<String, Object?> j) => VentaDelCliente(
        id: j['id']! as String,
        folio: j['folio'] as String?,
        fecha: j['fecha']! as String,
        tipo: j['tipo']! as String,
        formaDePago: j['forma_pago'] == null
            ? null
            : FormaDePago.deCodigo(j['forma_pago'] as String?),
        estado: j['estado']! as String,
        total: _d(j['total']),
        vendedor: j['vendedor']! as String,
      );

  final String id;
  final String? folio;
  final String fecha;

  /// 'contado'; 'credito' solo en las ventas del piloto.
  final String tipo;

  /// Nula solo en las ventas a crédito del piloto.
  final FormaDePago? formaDePago;
  final String estado;
  final Dinero total;
  final String vendedor;
}

class FichaDelCliente {
  const FichaDelCliente(this._j);

  factory FichaDelCliente.deJson(Map<String, Object?> j) => FichaDelCliente(j);

  final Map<String, Object?> _j;

  String get id => _j['id']! as String;
  String? get codigo => _j['codigo'] as String?;
  String get nombre => _j['nombre']! as String;
  String? get razonSocial => _j['razon_social'] as String?;
  String? get contacto => _j['contacto'] as String?;
  String? get telefono => _j['telefono'] as String?;
  String? get direccion => _j['direccion'] as String?;
  String? get referencias => _j['referencias'] as String?;
  String? get ruta => _j['ruta'] as String?;
  String get estatus => _j['estatus']! as String;

  /// Dónde está: lo que usa la geocerca de la venta y el mapa del tablero.
  double? get lat => _coordenada(_j['lat']);
  double? get lng => _coordenada(_j['lng']);

  /// 'gps' si se tomó parado en el negocio; 'manual' si se escribió o corrigió.
  String? get ubicacionOrigen => _j['ubicacion_origen'] as String?;
  bool get conUbicacion => lat != null && lng != null;

  /// Si quien la ve puede fijar o corregir la ubicación (§84). Un servidor
  /// anterior no lo manda: falso, y la ficha solo la muestra.
  bool get puedeUbicar => _j['puede_ubicar'] as bool? ?? false;
  Dinero get compradoMes => _d(_j['comprado_mes']);
  Dinero get compradoAnio => _d(_j['comprado_anio']);
  List<VentaDelCliente> get ventas =>
      [for (final v in _j['ventas']! as List) VentaDelCliente.deJson((v as Map).cast())];
  String? get mensaje => _j['mensaje'] as String?;
}

class ClienteClientesDeOficina {
  const ClienteClientesDeOficina(this._transporte);

  final Transporte _transporte;

  /// Con [agrupar] la lista es como la del dashboard (ADR 0002 §122): sin
  /// [ruta], [dia] ni búsqueda trae solo el resumen de rutas; con ellos, esas
  /// tiendas en el orden de su recorrido. [ruta] es la clave de la ruta («sin»
  /// las que no tienen); [dia], 0–6 (1 = lunes) o «sin».
  Future<ListaDeClientesDeOficina> lista({
    String filtro = 'todos',
    String q = '',
    String ruta = '',
    String dia = '',
    bool agrupar = false,
  }) async {
    final r = await _transporte.obtener('/v1/oficina/clientes', parametros: {
      'filtro': filtro,
      if (q.trim().isNotEmpty) 'q': q.trim(),
      if (ruta.isNotEmpty) 'ruta': ruta,
      if (dia.isNotEmpty) 'dia': dia,
      if (agrupar) 'agrupar': '1',
    });
    _revisar(r);
    return ListaDeClientesDeOficina.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<FichaDelCliente> ficha(String id) async {
    final r = await _transporte.obtener('/v1/oficina/clientes/$id');
    _revisar(r);
    return FichaDelCliente.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  /// Fija o corrige la ubicación (§84): la del GPS del teléfono o la escrita a
  /// mano. Devuelve la ficha como quedó, con el mensaje del servidor.
  Future<FichaDelCliente> ubicar(String id, Ubicacion ubicacion) async {
    final r = await _transporte.post('/v1/oficina/clientes/$id/ubicacion', {
      'lat': ubicacion.latTexto,
      'lng': ubicacion.lngTexto,
      'origen': ubicacion.origen.codigo,
      if (ubicacion.precisionMetros != null)
        'precision_m': ubicacion.precisionMetros!.toStringAsFixed(2),
    });
    _revisar(r);
    return FichaDelCliente.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw const SinPermisoDeVendedores();
    if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
      throw const ServidorSinEstaFuncion();
    }
    if (r.codigo >= 400 && r.codigo < 500) {
      String detalle = r.cuerpo;
      try {
        final j = jsonDecode(r.cuerpo);
        if (j is Map && j['detail'] is String) detalle = j['detail'] as String;
      } on FormatException {
        // Crudo.
      }
      throw CargaRechazada(r.codigo, detalle);
    }
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }
}
