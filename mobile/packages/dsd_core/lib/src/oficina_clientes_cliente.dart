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
}

class ListaDeClientesDeOficina {
  const ListaDeClientesDeOficina({
    required this.filtro,
    required this.clientes,
    required this.recortado,
    required this.conteos,
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
      );

  final String filtro;
  final List<ClienteDeOficina> clientes;
  final bool recortado;

  /// Cuántos hay en cada filtro: todos, prospectos, sin_ubicacion.
  final Map<String, int> conteos;
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
  Dinero get compradoMes => _d(_j['comprado_mes']);
  Dinero get compradoAnio => _d(_j['comprado_anio']);
  List<VentaDelCliente> get ventas =>
      [for (final v in _j['ventas']! as List) VentaDelCliente.deJson((v as Map).cast())];
  String? get mensaje => _j['mensaje'] as String?;
}

class ClienteClientesDeOficina {
  const ClienteClientesDeOficina(this._transporte);

  final Transporte _transporte;

  Future<ListaDeClientesDeOficina> lista({String filtro = 'todos', String q = ''}) async {
    final r = await _transporte.obtener('/v1/oficina/clientes', parametros: {
      'filtro': filtro,
      if (q.trim().isNotEmpty) 'q': q.trim(),
    });
    _revisar(r);
    return ListaDeClientesDeOficina.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<FichaDelCliente> ficha(String id) async {
    final r = await _transporte.obtener('/v1/oficina/clientes/$id');
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
