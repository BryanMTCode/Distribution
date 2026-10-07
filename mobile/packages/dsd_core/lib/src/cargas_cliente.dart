/// Cargar el camión desde la app: el cliente de `/v1/cargas`.
///
/// Solo lo usa el portal de la oficina —admin, supervisor, gerente—. El
/// vendedor no tiene `inventario.cargar`: cargarse su propio camión sería
/// firmar su propia entrega, y el servidor contesta 403 aunque alguien arme la
/// petición a mano.
///
/// Necesita red, como el tablero, y por la misma razón: confirmar una carga
/// mueve la bodega y le avisa al teléfono del vendedor, y eso solo lo puede
/// hacer el servidor. No hay copia local ni cola: si no hay señal, se dice.
///
/// Las cantidades viajan como texto («96.000»), igual que en todo el contrato,
/// y se quedan como texto: esta pantalla solo las muestra.
library;

import 'dart:convert';

import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';

/// El servidor dijo que no a algo que la pantalla puede explicar: una carga ya
/// confirmada, bloqueos sin motivo, un vendedor sin camión. Trae el texto del
/// servidor, que es el que dice qué hacer.
class CargaRechazada implements Exception {
  const CargaRechazada(this.codigo, this.detalle);

  final int codigo;
  final String detalle;

  @override
  String toString() => detalle;
}

/// Sin `inventario.cargar`.
class SinPermisoDeCargar implements Exception {
  const SinPermisoDeCargar();
}

class OpcionDeCarga {
  const OpcionDeCarga({
    required this.id,
    required this.codigo,
    required this.nombre,
    this.camion,
  });

  factory OpcionDeCarga.deJson(Map<String, Object?> j) => OpcionDeCarga(
        id: j['id']! as String,
        codigo: j['codigo']! as String,
        nombre: j['nombre']! as String,
        camion: j['camion'] as String?,
      );

  final String id;
  final String codigo;
  final String nombre;
  final String? camion;
}

class OpcionesDeCarga {
  const OpcionesDeCarga({required this.vendedores, required this.bodegas});

  factory OpcionesDeCarga.deJson(Map<String, Object?> j) => OpcionesDeCarga(
        vendedores: _lista(j['vendedores'], OpcionDeCarga.deJson),
        bodegas: _lista(j['bodegas'], OpcionDeCarga.deJson),
      );

  final List<OpcionDeCarga> vendedores;
  final List<OpcionDeCarga> bodegas;
}

class ResumenDeCarga {
  const ResumenDeCarga({
    required this.id,
    required this.folio,
    required this.estado,
    required this.fecha,
    required this.vendedor,
    required this.camion,
    required this.renglones,
    required this.piezas,
  });

  factory ResumenDeCarga.deJson(Map<String, Object?> j) => ResumenDeCarga(
        id: j['id']! as String,
        folio: j['folio']! as String,
        estado: j['estado']! as String,
        fecha: j['fecha_operativa']! as String,
        vendedor: j['vendedor']! as String,
        camion: j['camion']! as String,
        renglones: (j['renglones']! as num).toInt(),
        piezas: j['piezas']! as String,
      );

  final String id;
  final String folio;
  final String estado;
  final String fecha;
  final String vendedor;
  final String camion;
  final int renglones;
  final String piezas;
}

class RenglonDeCarga {
  const RenglonDeCarga({
    required this.id,
    required this.nombre,
    required this.sku,
    required this.unidadBase,
    required this.cantidad,
    required this.enBodega,
  });

  factory RenglonDeCarga.deJson(Map<String, Object?> j) => RenglonDeCarga(
        id: j['id']! as String,
        nombre: j['nombre']! as String,
        sku: j['sku']! as String,
        unidadBase: j['unidad_base']! as String,
        cantidad: j['cantidad']! as String,
        enBodega: j['en_bodega']! as String,
      );

  final String id;
  final String nombre;
  final String sku;
  final String unidadBase;
  final String cantidad;
  final String enBodega;
}

class PresentacionDeCarga {
  const PresentacionDeCarga({required this.unidad, required this.factor});

  factory PresentacionDeCarga.deJson(Map<String, Object?> j) => PresentacionDeCarga(
        unidad: j['unidad']! as String,
        factor: j['factor']! as String,
      );

  final String unidad;
  final String factor;
}

/// Un producto que la bodega tiene y se puede subir.
class ProductoEnBodega {
  const ProductoEnBodega({
    required this.productoId,
    required this.nombre,
    required this.sku,
    required this.unidadBase,
    required this.enBodega,
    required this.yaEnLaCarga,
    required this.presentaciones,
    this.porOmision,
  });

  factory ProductoEnBodega.deJson(Map<String, Object?> j) => ProductoEnBodega(
        productoId: j['producto_id']! as String,
        nombre: j['nombre']! as String,
        sku: j['sku']! as String,
        unidadBase: j['unidad_base']! as String,
        enBodega: j['en_bodega']! as String,
        yaEnLaCarga: j['ya_en_la_carga']! as String,
        presentaciones: _lista(j['presentaciones'], PresentacionDeCarga.deJson),
        porOmision: j['por_omision'] as String?,
      );

  final String productoId;
  final String nombre;
  final String sku;
  final String unidadBase;
  final String enBodega;
  final String yaEnLaCarga;
  final List<PresentacionDeCarga> presentaciones;
  final String? porOmision;
}

class DetalleDeCarga {
  const DetalleDeCarga({
    required this.id,
    required this.folio,
    required this.estado,
    required this.fecha,
    required this.vendedor,
    required this.camion,
    required this.bodega,
    required this.editable,
    required this.renglones,
    required this.surtido,
    required this.bloqueos,
    this.mensaje,
  });

  factory DetalleDeCarga.deJson(Map<String, Object?> j) => DetalleDeCarga(
        id: j['id']! as String,
        folio: j['folio']! as String,
        estado: j['estado']! as String,
        fecha: j['fecha_operativa']! as String,
        vendedor: j['vendedor']! as String,
        camion: j['camion']! as String,
        bodega: j['bodega']! as String,
        editable: j['editable']! as bool,
        renglones: _lista(j['renglones'], RenglonDeCarga.deJson),
        surtido: _lista(j['surtido'], ProductoEnBodega.deJson),
        bloqueos: ((j['bloqueos'] ?? const <Object?>[]) as List).cast<String>(),
        mensaje: j['mensaje'] as String?,
      );

  final String id;
  final String folio;
  final String estado;
  final String fecha;
  final String vendedor;
  final String camion;
  final String bodega;
  final bool editable;
  final List<RenglonDeCarga> renglones;
  final List<ProductoEnBodega> surtido;
  final List<String> bloqueos;

  /// Lo que dijo el servidor de lo último que se hizo: «se agregaron 3», «NO
  /// entraron: …», «confirmada». Se muestra tal cual.
  final String? mensaje;
}

/// Un renglón por capturar: cuántos bultos de qué presentación.
class PedidoDeCarga {
  const PedidoDeCarga({
    required this.productoId,
    required this.unidad,
    required this.cantidad,
  });

  final String productoId;
  final String unidad;

  /// Texto, como lo tecleó la persona: el servidor lo valida con las mismas
  /// reglas que el panel («2.5» cajas se rechaza con su mensaje).
  final String cantidad;

  Map<String, Object?> aJson() =>
      {'producto_id': productoId, 'unidad': unidad, 'cantidad': cantidad};
}

class ClienteCargas {
  const ClienteCargas(this._transporte);

  final Transporte _transporte;

  Future<OpcionesDeCarga> opciones() async =>
      OpcionesDeCarga.deJson(await _get('/v1/cargas/opciones') as Map<String, Object?>);

  Future<List<ResumenDeCarga>> abiertas() async =>
      _lista(await _get('/v1/cargas'), ResumenDeCarga.deJson);

  Future<DetalleDeCarga> ver(String id) async =>
      DetalleDeCarga.deJson(await _get('/v1/cargas/$id') as Map<String, Object?>);

  Future<DetalleDeCarga> abrir({required String vendedorId, required String bodegaId}) =>
      _post('/v1/cargas', {'vendedor_id': vendedorId, 'almacen_origen_id': bodegaId});

  Future<DetalleDeCarga> agregar(String id, List<PedidoDeCarga> pedidos) => _post(
        '/v1/cargas/$id/renglones',
        {'renglones': [for (final p in pedidos) p.aJson()]},
      );

  Future<DetalleDeCarga> quitar(String id, String renglonId) =>
      _post('/v1/cargas/$id/renglones/$renglonId/quitar', const {});

  Future<DetalleDeCarga> confirmar(String id, {String? motivoForzado}) => _post(
        '/v1/cargas/$id/confirmar',
        {if (motivoForzado != null) 'motivo_forzado': motivoForzado},
      );

  Future<DetalleDeCarga> cancelar(String id) =>
      _post('/v1/cargas/$id/cancelar', const {});

  Future<Object?> _get(String ruta) async {
    final r = await _transporte.obtener(ruta);
    _revisar(r);
    return jsonDecode(r.cuerpo);
  }

  Future<DetalleDeCarga> _post(String ruta, Map<String, Object?> cuerpo) async {
    final r = await _transporte.post(ruta, cuerpo);
    _revisar(r);
    return DetalleDeCarga.deJson(jsonDecode(r.cuerpo) as Map<String, Object?>);
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw const SinPermisoDeCargar();
    if (r.codigo >= 400 && r.codigo < 500) {
      throw CargaRechazada(r.codigo, _detalle(r.cuerpo));
    }
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

/// «96.000» → «96»; «1.500» → «1.5». Las cantidades de la carga son piezas
/// enteras casi siempre, y tres ceros en cada renglón estorban al leer.
String cantidadLegible(String cantidad) {
  if (!cantidad.contains('.')) return cantidad;
  final sinCeros = cantidad.replaceFirst(RegExp(r'0+$'), '');
  return sinCeros.endsWith('.') ? sinCeros.substring(0, sinCeros.length - 1) : sinCeros;
}

List<T> _lista<T>(Object? crudo, T Function(Map<String, Object?>) de) => [
      for (final e in (crudo ?? const <Object?>[]) as List)
        de((e as Map).cast<String, Object?>()),
    ];
