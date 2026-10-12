/// Las ventas de mayoreo desde la app de la oficina: el cliente de
/// `/v1/mayoreo` (ADR 0002 §105).
///
/// «El administrador puede hacer ventas a mayoreo al precio que él decida, y
/// los clientes él los maneja.» En línea, como las cargas: la venta saca la
/// mercancía de la bodega en ese momento, y eso solo lo hace el servidor.
library;

import 'dart:convert';

import 'oficina_rechazo.dart';
import 'transporte.dart';

class ClienteDeMayoreo {
  const ClienteDeMayoreo({
    required this.id,
    required this.nombre,
    this.codigo,
    this.telefono,
    this.direccion,
    this.ventas = 0,
    this.vendido = '0.00',
  });

  factory ClienteDeMayoreo.deJson(Map<String, Object?> j) => ClienteDeMayoreo(
        id: j['id']! as String,
        codigo: j['codigo'] as String?,
        nombre: j['nombre_comercial']! as String,
        telefono: j['telefono'] as String?,
        direccion: j['direccion'] as String?,
        ventas: (j['ventas'] as num?)?.toInt() ?? 0,
        vendido: j['vendido'] as String? ?? '0.00',
      );

  final String id;
  final String? codigo;
  final String nombre;
  final String? telefono;
  final String? direccion;
  final int ventas;
  final String vendido;
}

class BodegaDeMayoreo {
  const BodegaDeMayoreo(this.id, this.nombre);

  final String id;
  final String nombre;
}

class OpcionesDeMayoreo {
  const OpcionesDeMayoreo(this.clientes, this.bodegas);

  final List<ClienteDeMayoreo> clientes;
  final List<BodegaDeMayoreo> bodegas;
}

class PresentacionDeMayoreo {
  const PresentacionDeMayoreo({required this.unidad, required this.factor, this.precioLista});

  final String unidad;

  /// Piezas por presentación, como texto («24.000»).
  final String factor;

  /// El de la lista general, como referencia; nulo si no tiene.
  final String? precioLista;
}

class ProductoDeBodega {
  const ProductoDeBodega({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.existencia,
    required this.presentaciones,
    this.familia,
  });

  factory ProductoDeBodega.deJson(Map<String, Object?> j) => ProductoDeBodega(
        productoId: j['producto_id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        familia: j['familia'] as String?,
        existencia: j['existencia']! as String,
        presentaciones: [
          for (final p in j['presentaciones']! as List)
            PresentacionDeMayoreo(
              unidad: (p as Map)['unidad']! as String,
              factor: p['factor']! as String,
              precioLista: p['precio_lista'] as String?,
            ),
        ],
      );

  final String productoId;
  final String sku;
  final String nombre;
  final String? familia;

  /// Piezas en la bodega, como texto.
  final String existencia;
  final List<PresentacionDeMayoreo> presentaciones;
}

/// Un renglón de la venta: cuántas de qué presentación, y a qué precio.
class RenglonDeMayoreo {
  const RenglonDeMayoreo({
    required this.productoId,
    required this.unidad,
    required this.bultos,
    required this.precio,
  });

  final String productoId;
  final String unidad;
  final int bultos;

  /// Por presentación, como texto («250.00»).
  final String precio;

  Map<String, Object?> aJson() => {
        'producto_id': productoId,
        'unidad': unidad,
        'bultos': '$bultos',
        'precio': precio,
      };
}

class VentaDeMayoreoResumen {
  const VentaDeMayoreoResumen({
    required this.id,
    required this.folio,
    required this.fecha,
    required this.estado,
    required this.total,
    required this.cliente,
    required this.piezas,
    required this.formaPago,
  });

  factory VentaDeMayoreoResumen.deJson(Map<String, Object?> j) => VentaDeMayoreoResumen(
        id: j['id']! as String,
        folio: j['folio']! as String,
        fecha: j['fecha']! as String,
        estado: j['estado']! as String,
        total: j['total']! as String,
        cliente: j['cliente']! as String,
        piezas: j['piezas']! as String,
        formaPago: j['forma_pago']! as String,
      );

  final String id;
  final String folio;
  final String fecha;
  final String estado;
  final String total;
  final String cliente;
  final String piezas;
  final String formaPago;
}

class VentasDeMayoreo {
  const VentasDeMayoreo(this.ventas, this.total);

  final List<VentaDeMayoreoResumen> ventas;
  final String total;
}

class VentaDeMayoreo {
  const VentaDeMayoreo({
    required this.id,
    required this.folio,
    required this.estado,
    required this.total,
    required this.ticket,
    this.motivoCancelacion,
  });

  factory VentaDeMayoreo.deJson(Map<String, Object?> j) => VentaDeMayoreo(
        id: j['id']! as String,
        folio: j['folio']! as String,
        estado: j['estado']! as String,
        total: j['total']! as String,
        ticket: j['ticket']! as String,
        motivoCancelacion: j['motivo_cancelacion'] as String?,
      );

  final String id;
  final String folio;
  final String estado;
  final String total;

  /// Listo para compartir por WhatsApp.
  final String ticket;
  final String? motivoCancelacion;
}

class ClienteMayoreo {
  const ClienteMayoreo(this._transporte);

  final Transporte _transporte;

  Future<OpcionesDeMayoreo> opciones() async {
    final j = await _get('/v1/mayoreo/opciones') as Map;
    return OpcionesDeMayoreo(
      [for (final c in j['clientes']! as List) ClienteDeMayoreo.deJson((c as Map).cast())],
      [
        for (final b in j['bodegas']! as List)
          BodegaDeMayoreo((b as Map)['id']! as String, b['nombre']! as String),
      ],
    );
  }

  Future<List<ProductoDeBodega>> surtido(String bodegaId) async => [
        for (final p in await _get('/v1/mayoreo/surtido/$bodegaId') as List)
          ProductoDeBodega.deJson((p as Map).cast()),
      ];

  Future<ClienteDeMayoreo> altaDeCliente({
    required String nombre,
    String? telefono,
    String? calle,
    String? colonia,
  }) async =>
      ClienteDeMayoreo.deJson(
        (await _post('/v1/mayoreo/clientes', {
          'nombre': nombre.trim(),
          if (telefono != null && telefono.trim().isNotEmpty) 'telefono': telefono.trim(),
          if (calle != null && calle.trim().isNotEmpty) 'calle': calle.trim(),
          if (colonia != null && colonia.trim().isNotEmpty) 'colonia': colonia.trim(),
        }) as Map)
            .cast(),
      );

  /// Las ventas de un periodo: el del tablero (`hoy`, `semana`…) o, con
  /// `desde` y `hasta`, un rango a mano.
  Future<VentasDeMayoreo> ventas({String periodo = 'hoy', String? desde, String? hasta}) async {
    final r = await _transporte.obtener('/v1/mayoreo', parametros: {
      if (desde != null && hasta != null) ...{
        'periodo': 'rango',
        'desde': desde,
        'hasta': hasta,
      } else
        'periodo': periodo,
    });
    revisarRespuestaDeOficina(r);
    final j = jsonDecode(r.cuerpo) as Map;
    return VentasDeMayoreo(
      [for (final v in j['ventas']! as List) VentaDeMayoreoResumen.deJson((v as Map).cast())],
      j['total']! as String,
    );
  }

  Future<VentaDeMayoreo> vender({
    required String clienteId,
    required String bodegaId,
    required List<RenglonDeMayoreo> renglones,
    String formaPago = 'efectivo',
    String? referenciaPago,
    String? nota,
  }) async =>
      VentaDeMayoreo.deJson(
        (await _post('/v1/mayoreo', {
          'cliente_id': clienteId,
          'almacen_id': bodegaId,
          'renglones': [for (final r in renglones) r.aJson()],
          'forma_pago': formaPago,
          if (referenciaPago != null && referenciaPago.trim().isNotEmpty)
            'referencia_pago': referenciaPago.trim(),
          if (nota != null && nota.trim().isNotEmpty) 'nota': nota.trim(),
        }) as Map)
            .cast(),
      );

  Future<VentaDeMayoreo> ver(String id) async =>
      VentaDeMayoreo.deJson((await _get('/v1/mayoreo/$id') as Map).cast());

  Future<VentaDeMayoreo> cancelar(String id, String motivo) async => VentaDeMayoreo.deJson(
        (await _post('/v1/mayoreo/$id/cancelar', {'motivo': motivo.trim()}) as Map).cast(),
      );

  Future<Object?> _get(String ruta) async {
    final r = await _transporte.obtener(ruta);
    revisarRespuestaDeOficina(r);
    return jsonDecode(r.cuerpo);
  }

  Future<Object?> _post(String ruta, Map<String, Object?> cuerpo) async {
    final r = await _transporte.post(ruta, cuerpo);
    revisarRespuestaDeOficina(r);
    return jsonDecode(r.cuerpo);
  }
}
