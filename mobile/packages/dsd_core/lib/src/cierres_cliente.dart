/// Los cierres de los vendedores, para la app del gerente (`/v1/cierres`).
///
/// El vendedor manda su corte y la carga que pide para mañana; el gerente los
/// ve juntos y los acepta: el corte se cierra y la carga se confirma (ADR 0002
/// §82). Necesita red, como el resto de la oficina.
library;

import 'dart:convert';

import 'cargas_cliente.dart' show CargaRechazada, SinPermisoDeCargar;
import 'dinero.dart';
import 'precio.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'tickets_compartibles.dart';
import 'transporte.dart';
import 'vendedores_cliente.dart' show ServidorSinEstaFuncion;

Dinero _d(Object? v) => Dinero.deTexto(v! as String);
Cantidad _c(Object? v) => Cantidad.deTexto(v! as String);
DateTime? _momento(Object? v) => v == null ? null : DateTime.parse(v as String).toLocal();

/// Un producto del corte: lo que contó el vendedor contra lo que el sistema cree.
class RenglonContado {
  const RenglonContado({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.contada,
    required this.contado,
    required this.sistema,
    required this.diferencia,
  });

  factory RenglonContado.deJson(Map<String, Object?> j) => RenglonContado(
        productoId: j['producto_id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        contada: _c(j['contada']),
        contado: j['contado']! as bool,
        sistema: _c(j['sistema']),
        diferencia: _c(j['diferencia']),
      );

  final String productoId;
  final String sku;
  final String nombre;
  final String unidadBase;
  final Cantidad contada;

  /// Falso si el vendedor no lo contó: al cerrar vale cero, o sea faltante.
  final bool contado;
  final Cantidad sistema;

  /// Negativo es faltante: lo que se le cobra.
  final Cantidad diferencia;
}

class CorteRecibido {
  const CorteRecibido({
    required this.id,
    required this.fechaOperativa,
    required this.estado,
    required this.efectivoDeclarado,
    required this.efectivoEsperado,
    required this.diferenciaEfectivo,
    required this.recibidoEn,
    required this.renglones,
    this.observaciones,
    this.liquidacionFolio,
    this.nota,
  });

  factory CorteRecibido.deJson(Map<String, Object?> j) => CorteRecibido(
        id: j['id']! as String,
        fechaOperativa: j['fecha_operativa']! as String,
        estado: j['estado']! as String,
        efectivoDeclarado: _d(j['efectivo_declarado']),
        efectivoEsperado: _d(j['efectivo_esperado']),
        diferenciaEfectivo: _d(j['diferencia_efectivo']),
        observaciones: j['observaciones'] as String?,
        recibidoEn: _momento(j['recibido_en'])!,
        liquidacionFolio: j['liquidacion_folio'] as String?,
        nota: j['nota'] as String?,
        renglones: [
          for (final r in (j['renglones'] as List?) ?? const [])
            RenglonContado.deJson((r as Map).cast()),
        ],
      );

  final String id;
  final String fechaOperativa;

  /// 'pendiente' | 'cerrado' | 'reemplazado'.
  final String estado;
  final Dinero efectivoDeclarado;
  final Dinero efectivoEsperado;

  /// Negativo: faltan. Positivo: sobran.
  final Dinero diferenciaEfectivo;
  final String? observaciones;
  final DateTime recibidoEn;
  final String? liquidacionFolio;
  final String? nota;
  final List<RenglonContado> renglones;

  bool get pendiente => estado == 'pendiente';
  List<RenglonContado> get conDiferencia =>
      renglones.where((r) => !r.diferencia.esCero).toList();
}

class RenglonSolicitado {
  const RenglonSolicitado({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.unidad,
    required this.factor,
    required this.bultos,
    required this.cantidad,
    required this.enBodega,
    this.cantidadAceptada,
  });

  factory RenglonSolicitado.deJson(Map<String, Object?> j) => RenglonSolicitado(
        productoId: j['producto_id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        unidad: j['unidad_codigo']! as String,
        // Llega como cantidad de tres decimales ('24.000'). Un factor no es dinero
        // ni se arrastra: pasar por `num` para leerlo no pierde nada.
        factor: Factor.deBase(num.parse(j['factor']! as String)),
        bultos: _c(j['bultos']),
        cantidad: _c(j['cantidad']),
        cantidadAceptada: j['cantidad_aceptada'] == null ? null : _c(j['cantidad_aceptada']),
        enBodega: _c(j['en_bodega']),
      );

  final String productoId;
  final String sku;
  final String nombre;
  final String unidadBase;
  final String unidad;
  final Factor factor;
  final Cantidad bultos;

  /// Lo pedido, en unidad base.
  final Cantidad cantidad;
  final Cantidad? cantidadAceptada;
  final Cantidad enBodega;

  /// La bodega no alcanza para lo pedido.
  bool get faltaEnBodega => enBodega < cantidad;

  RenglonDeTicket get paraElTicket => RenglonDeTicket(
        nombre: nombre,
        cantidadBase: cantidad,
        unidadBase: unidadBase,
        unidad: unidad,
        factor: factor,
        aceptadaBase: cantidadAceptada,
      );
}

class SolicitudRecibida {
  const SolicitudRecibida({
    required this.id,
    required this.fechaOperativa,
    required this.estado,
    required this.recibidoEn,
    required this.renglones,
    this.observaciones,
    this.resueltaEn,
    this.resueltaPor,
    this.motivo,
    this.cargaFolio,
    this.bodega,
  });

  factory SolicitudRecibida.deJson(Map<String, Object?> j) => SolicitudRecibida(
        id: j['id']! as String,
        fechaOperativa: j['fecha_operativa']! as String,
        estado: j['estado']! as String,
        observaciones: j['observaciones'] as String?,
        recibidoEn: _momento(j['recibido_en'])!,
        resueltaEn: _momento(j['resuelta_en']),
        resueltaPor: j['resuelta_por'] as String?,
        motivo: j['motivo'] as String?,
        cargaFolio: j['carga_folio'] as String?,
        bodega: j['bodega'] as String?,
        renglones: [
          for (final r in (j['renglones'] as List?) ?? const [])
            RenglonSolicitado.deJson((r as Map).cast()),
        ],
      );

  final String id;

  /// PARA cuándo es la carga.
  final String fechaOperativa;

  /// 'pendiente' | 'aceptada' | 'rechazada' | 'reemplazada'.
  final String estado;
  final String? observaciones;
  final DateTime recibidoEn;
  final DateTime? resueltaEn;
  final String? resueltaPor;
  final String? motivo;
  final String? cargaFolio;

  /// De dónde sale: la bodega principal.
  final String? bodega;
  final List<RenglonSolicitado> renglones;

  bool get pendiente => estado == 'pendiente';
  bool get aceptada => estado == 'aceptada';
}

/// Lo que el gerente revisa de un jalón: el corte de un vendedor y lo que pidió.
class CierreDeVendedor {
  const CierreDeVendedor({
    required this.vendedorId,
    required this.vendedorCodigo,
    required this.vendedor,
    this.camion,
    this.corte,
    this.solicitud,
    this.mensaje,
  });

  factory CierreDeVendedor.deJson(Map<String, Object?> j) => CierreDeVendedor(
        vendedorId: j['vendedor_id']! as String,
        vendedorCodigo: j['vendedor_codigo']! as String,
        vendedor: j['vendedor']! as String,
        camion: j['camion'] as String?,
        corte: j['corte'] == null ? null : CorteRecibido.deJson((j['corte']! as Map).cast()),
        solicitud: j['solicitud'] == null
            ? null
            : SolicitudRecibida.deJson((j['solicitud']! as Map).cast()),
        mensaje: j['mensaje'] as String?,
      );

  final String vendedorId;
  final String vendedorCodigo;
  final String vendedor;
  final String? camion;
  final CorteRecibido? corte;
  final SolicitudRecibida? solicitud;
  final String? mensaje;

  /// Una llave estable para la pantalla.
  String get clave => solicitud?.id ?? corte!.id;

  /// El ticket de la carga, para compartirlo después de aceptarla.
  String? get ticketDeLaCarga {
    final s = solicitud;
    if (s == null) return null;
    return textoDeLaCarga(
      TicketDeCarga(
        id: s.id,
        vendedor: vendedor,
        codigoVendedor: vendedorCodigo,
        paraElDia: s.fechaOperativa,
        estado: s.estado,
        cargaFolio: s.cargaFolio,
        motivo: s.motivo,
        observaciones: s.observaciones,
        bodega: s.bodega,
        renglones: [for (final r in s.renglones) r.paraElTicket],
      ),
    );
  }
}

class Cierres {
  const Cierres({required this.pendientes, required this.recientes});

  factory Cierres.deJson(Map<String, Object?> j) => Cierres(
        pendientes: [
          for (final c in (j['pendientes'] as List?) ?? const [])
            CierreDeVendedor.deJson((c as Map).cast()),
        ],
        recientes: [
          for (final c in (j['recientes'] as List?) ?? const [])
            CierreDeVendedor.deJson((c as Map).cast()),
        ],
      );

  final List<CierreDeVendedor> pendientes;
  final List<CierreDeVendedor> recientes;
}

class ClienteCierres {
  const ClienteCierres(this._transporte);

  final Transporte _transporte;

  Future<Cierres> lista() async {
    final r = await _transporte.obtener('/v1/cierres');
    _revisar(r);
    return Cierres.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  /// Cierra el corte y confirma la carga. `bultos` corrige lo pedido (de id de
  /// producto a bultos de la misma presentación; «0» lo quita).
  Future<CierreDeVendedor> aceptar({
    String? corteId,
    String? solicitudId,
    Map<String, String> bultos = const {},
  }) =>
      _post('/v1/cierres/aceptar', {
        'corte_id': corteId,
        'solicitud_id': solicitudId,
        'bultos': bultos,
      });

  Future<CierreDeVendedor> rechazar(String solicitudId, {required String motivo}) =>
      _post('/v1/cierres/solicitudes/$solicitudId/rechazar', {'motivo': motivo});

  Future<CierreDeVendedor> _post(String ruta, Map<String, Object?> cuerpo) async {
    final r = await _transporte.post(ruta, cuerpo);
    _revisar(r);
    return CierreDeVendedor.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw const SinPermisoDeCargar();
    if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
      throw const ServidorSinEstaFuncion();
    }
    if (r.codigo >= 400 && r.codigo < 500) throw CargaRechazada(r.codigo, _detalle(r.cuerpo));
    throw ServidorConProblemas(r.codigo, r.cuerpo);
  }

  String _detalle(String cuerpo) {
    try {
      final json = jsonDecode(cuerpo);
      if (json is Map && json['detail'] is String) return json['detail'] as String;
    } on FormatException {
      // Cae al crudo.
    }
    return cuerpo;
  }
}
