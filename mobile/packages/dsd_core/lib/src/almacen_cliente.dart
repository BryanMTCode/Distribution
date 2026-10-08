/// El almacén desde la app de la oficina: el cliente de `/v1/almacen`.
///
/// · **Existencias** de cada bodega y cada camión, negativos incluidos.
/// · **Entradas** de mercancía (compra, inventario inicial, ajuste por conteo),
///   con las mismas reglas que el panel: el servidor usa las mismas funciones.
/// · **Traspasos** entre bodegas, en un paso.
///
/// Ver: `inventario.ver` y ser de la oficina. Escribir: `inventario.ajustar`
/// (admin, supervisor, gerente).
library;

import 'dart:convert';

import 'compras_sin_senal.dart' show CatalogoDeCompras, CompraCapturada, ProductoParaComprar;

import 'cargas_cliente.dart' show CargaRechazada, PresentacionDeCarga;
import 'dinero.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';
import 'vendedores_cliente.dart' show ServidorSinEstaFuncion;

/// Sin permiso para ver o mover el almacén.
class SinPermisoDeAlmacen implements Exception {
  const SinPermisoDeAlmacen();
}

List<PresentacionDeCarga> _presentaciones(Object? crudo) => [
      for (final p in (crudo ?? const <Object?>[]) as List)
        PresentacionDeCarga.deJson((p as Map).cast()),
    ];

Dinero? _dineroONulo(Object? texto) => texto == null ? null : Dinero.deTexto(texto as String);

// ---------------------------------------------------------------------------
// Existencias
// ---------------------------------------------------------------------------
class AlmacenResumen {
  const AlmacenResumen({
    required this.id,
    required this.codigo,
    required this.nombre,
    required this.tipo,
    required this.responsable,
    required this.productos,
    required this.piezas,
    required this.negativos,
  });

  factory AlmacenResumen.deJson(Map<String, Object?> j) => AlmacenResumen(
        id: j['id']! as String,
        codigo: j['codigo']! as String,
        nombre: j['nombre']! as String,
        tipo: j['tipo']! as String,
        responsable: j['responsable'] as String?,
        productos: (j['productos']! as num).toInt(),
        piezas: j['piezas']! as String,
        negativos: (j['negativos']! as num).toInt(),
      );

  final String id;
  final String codigo;
  final String nombre;

  /// 'bodega' o 'camion'.
  final String tipo;

  /// El vendedor del camión; las bodegas casi nunca tienen.
  final String? responsable;

  /// Productos con existencia distinta de cero.
  final int productos;

  /// Piezas en positivo, en texto del contrato («760.000»).
  final String piezas;

  /// Productos en negativo: algo se vendió o se movió sin estar en el sistema.
  final int negativos;

  bool get esBodega => tipo == 'bodega';
}

class ExistenciaDeProducto {
  const ExistenciaDeProducto({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.cantidad,
    required this.presentaciones,
  });

  factory ExistenciaDeProducto.deJson(Map<String, Object?> j) => ExistenciaDeProducto(
        productoId: j['producto_id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        cantidad: j['cantidad']! as String,
        presentaciones: _presentaciones(j['presentaciones']),
      );

  final String productoId;
  final String sku;
  final String nombre;
  final String unidadBase;
  final String cantidad;

  /// La más grande primero.
  final List<PresentacionDeCarga> presentaciones;

  bool get negativa => double.parse(cantidad) < 0;
}

class ExistenciasDelAlmacen {
  const ExistenciasDelAlmacen({required this.almacen, required this.existencias});

  factory ExistenciasDelAlmacen.deJson(Map<String, Object?> j) => ExistenciasDelAlmacen(
        almacen: AlmacenResumen.deJson((j['almacen']! as Map).cast()),
        existencias: [
          for (final e in j['existencias']! as List)
            ExistenciaDeProducto.deJson((e as Map).cast()),
        ],
      );

  final AlmacenResumen almacen;
  final List<ExistenciaDeProducto> existencias;
}

/// Un producto del catálogo, con lo que hay de él en el almacén que se pidió.
class ProductoParaCapturar {
  const ProductoParaCapturar({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.manejaLote,
    required this.existencia,
    required this.presentaciones,
  });

  factory ProductoParaCapturar.deJson(Map<String, Object?> j) => ProductoParaCapturar(
        productoId: j['producto_id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        manejaLote: j['maneja_lote']! as bool,
        existencia: j['existencia']! as String,
        presentaciones: _presentaciones(j['presentaciones']),
      );

  final String productoId;
  final String sku;
  final String nombre;
  final String unidadBase;
  final bool manejaLote;
  final String existencia;

  /// La más grande primero: es la que se cuenta al recibir.
  final List<PresentacionDeCarga> presentaciones;
}

/// Una bodega o un proveedor, para elegir.
class OpcionDeAlmacen {
  const OpcionDeAlmacen({required this.id, required this.nombre, this.codigo});

  factory OpcionDeAlmacen.deJson(Map<String, Object?> j) => OpcionDeAlmacen(
        id: j['id']! as String,
        codigo: j['codigo'] as String?,
        nombre: j['nombre']! as String,
      );

  final String id;
  final String? codigo;
  final String nombre;
}

// ---------------------------------------------------------------------------
// Entradas
// ---------------------------------------------------------------------------
class MotivoDeEntrada {
  const MotivoDeEntrada({
    required this.clave,
    required this.etiqueta,
    required this.explicacion,
  });

  factory MotivoDeEntrada.deJson(Map<String, Object?> j) => MotivoDeEntrada(
        clave: j['clave']! as String,
        etiqueta: j['etiqueta']! as String,
        explicacion: j['explicacion']! as String,
      );

  /// 'compra', 'inicial' o 'ajuste'.
  final String clave;
  final String etiqueta;
  final String explicacion;

  /// La compra exige costo por renglón y deja la cuenta por pagar.
  bool get exigeCosto => clave == 'compra';

  /// El inventario inicial exige nota: es el papel que explica de dónde salió
  /// todo el inventario del arranque.
  bool get exigeNota => clave == 'inicial';
}

class EntradaEnLista {
  const EntradaEnLista({
    required this.id,
    required this.folio,
    required this.motivo,
    required this.motivoEtiqueta,
    required this.estado,
    required this.proveedor,
    required this.referencia,
    required this.fecha,
    required this.bodega,
    required this.renglones,
    required this.piezas,
    required this.importeTotal,
  });

  factory EntradaEnLista.deJson(Map<String, Object?> j) => EntradaEnLista(
        id: j['id']! as String,
        folio: j['folio']! as String,
        motivo: j['motivo']! as String,
        motivoEtiqueta: j['motivo_etiqueta']! as String,
        estado: j['estado']! as String,
        proveedor: j['proveedor'] as String?,
        referencia: j['referencia'] as String?,
        fecha: j['fecha_operativa']! as String,
        bodega: j['bodega']! as String,
        renglones: (j['renglones']! as num).toInt(),
        piezas: j['piezas']! as String,
        importeTotal: _dineroONulo(j['importe_total']),
      );

  final String id;
  final String folio;
  final String motivo;
  final String motivoEtiqueta;

  /// 'borrador', 'confirmada' o 'cancelada'.
  final String estado;
  final String? proveedor;
  final String? referencia;
  final String fecha;
  final String bodega;
  final int renglones;
  final String piezas;
  final Dinero? importeTotal;
}

class ListaDeEntradas {
  const ListaDeEntradas({
    required this.entradas,
    required this.bodegas,
    required this.proveedores,
    required this.motivos,
  });

  factory ListaDeEntradas.deJson(Map<String, Object?> j) => ListaDeEntradas(
        entradas: [
          for (final e in j['entradas']! as List) EntradaEnLista.deJson((e as Map).cast()),
        ],
        bodegas: [
          for (final b in j['bodegas']! as List) OpcionDeAlmacen.deJson((b as Map).cast()),
        ],
        proveedores: [
          for (final p in j['proveedores']! as List) OpcionDeAlmacen.deJson((p as Map).cast()),
        ],
        motivos: [
          for (final m in j['motivos']! as List) MotivoDeEntrada.deJson((m as Map).cast()),
        ],
      );

  final List<EntradaEnLista> entradas;
  final List<OpcionDeAlmacen> bodegas;
  final List<OpcionDeAlmacen> proveedores;
  final List<MotivoDeEntrada> motivos;
}

class RenglonDeEntrada {
  const RenglonDeEntrada({
    required this.id,
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.cantidad,
    required this.unidad,
    required this.bultos,
    required this.costoUnitario,
    required this.importe,
    required this.lote,
    required this.existencia,
    required this.proyectado,
  });

  factory RenglonDeEntrada.deJson(Map<String, Object?> j) => RenglonDeEntrada(
        id: j['id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        cantidad: j['cantidad']! as String,
        unidad: j['unidad_codigo'] as String?,
        bultos: j['unidades_capturadas'] as String?,
        costoUnitario: j['costo_unitario']?.toString(),
        importe: _dineroONulo(j['importe']),
        lote: j['lote'] as String?,
        existencia: j['existencia']! as String,
        proyectado: j['proyectado']! as String,
      );

  final String id;
  final String sku;
  final String nombre;
  final String unidadBase;

  /// En piezas (unidad base).
  final String cantidad;

  /// Como se capturó: «10» de «CAJA».
  final String? unidad;
  final String? bultos;

  /// Costo por pieza, como lo guardó el servidor (más de dos decimales).
  final String? costoUnitario;
  final Dinero? importe;
  final String? lote;

  /// Lo que había en la bodega y lo que habrá al confirmar.
  final String existencia;
  final String proyectado;
}

class CuentaPorPagar {
  const CuentaPorPagar({
    required this.proveedor,
    required this.importeOriginal,
    required this.saldo,
    required this.estado,
    required this.vence,
  });

  factory CuentaPorPagar.deJson(Map<String, Object?> j) => CuentaPorPagar(
        proveedor: j['proveedor']! as String,
        importeOriginal: Dinero.deTexto(j['importe_original']! as String),
        saldo: Dinero.deTexto(j['saldo']! as String),
        estado: j['estado']! as String,
        vence: j['fecha_vencimiento']! as String,
      );

  final String proveedor;
  final Dinero importeOriginal;
  final Dinero saldo;
  final String estado;
  final String vence;
}

class DetalleDeEntrada {
  const DetalleDeEntrada({
    required this.id,
    required this.folio,
    required this.motivo,
    required this.motivoEtiqueta,
    required this.estado,
    required this.editable,
    required this.exigeCosto,
    required this.proveedor,
    required this.referencia,
    required this.nota,
    required this.fecha,
    required this.bodega,
    required this.bodegaId,
    required this.renglones,
    required this.totalPiezas,
    required this.importeCapturado,
    required this.sinCosto,
    required this.cuenta,
    this.mensaje,
  });

  factory DetalleDeEntrada.deJson(Map<String, Object?> j) => DetalleDeEntrada(
        id: j['id']! as String,
        folio: j['folio']! as String,
        motivo: j['motivo']! as String,
        motivoEtiqueta: j['motivo_etiqueta']! as String,
        estado: j['estado']! as String,
        editable: j['editable']! as bool,
        exigeCosto: j['exige_costo']! as bool,
        proveedor: j['proveedor'] as String?,
        referencia: j['referencia'] as String?,
        nota: j['nota'] as String?,
        fecha: j['fecha_operativa']! as String,
        bodega: j['bodega']! as String,
        bodegaId: j['almacen_destino_id']! as String,
        renglones: [
          for (final r in j['renglones']! as List) RenglonDeEntrada.deJson((r as Map).cast()),
        ],
        totalPiezas: j['total_piezas']! as String,
        importeCapturado: Dinero.deTexto(j['importe_capturado']! as String),
        sinCosto: (j['sin_costo']! as num).toInt(),
        cuenta: j['cuenta'] == null
            ? null
            : CuentaPorPagar.deJson((j['cuenta']! as Map).cast()),
        mensaje: j['mensaje'] as String?,
      );

  final String id;
  final String folio;
  final String motivo;
  final String motivoEtiqueta;
  final String estado;

  /// Solo el borrador se edita.
  final bool editable;
  final bool exigeCosto;
  final String? proveedor;
  final String? referencia;
  final String? nota;
  final String fecha;
  final String bodega;
  final String bodegaId;
  final List<RenglonDeEntrada> renglones;
  final String totalPiezas;
  final Dinero importeCapturado;

  /// Renglones de una compra todavía sin costo: no deja confirmar.
  final int sinCosto;

  /// La deuda con el proveedor que dejó la compra al confirmarse.
  final CuentaPorPagar? cuenta;
  final String? mensaje;
}

/// Lo que se teclea para abrir una entrada.
class NuevaEntrada {
  const NuevaEntrada({
    required this.bodegaId,
    required this.motivo,
    this.proveedorId,
    this.proveedor = '',
    this.referencia = '',
    this.nota = '',
  });

  final String bodegaId;
  final String motivo;

  /// El del catálogo; si no, el nombre escrito a mano (se copia tal cual).
  final String? proveedorId;
  final String proveedor;
  final String referencia;
  final String nota;

  Map<String, Object?> aJson() => {
        'almacen_destino_id': bodegaId,
        'motivo': motivo,
        if (proveedorId != null) 'proveedor_id': proveedorId,
        'proveedor': proveedor,
        'referencia': referencia,
        'nota': nota,
      };
}

/// Un renglón por agregar a la entrada.
class RenglonPorRecibir {
  const RenglonPorRecibir({
    required this.sku,
    required this.unidad,
    required this.cantidad,
    this.costo = '',
    this.lote = '',
    this.caducidad = '',
  });

  final String sku;
  final String unidad;

  /// Bultos, como se tecleó: el servidor los valida («1.5» cajas no).
  final String cantidad;

  /// Por bulto de la presentación elegida, como en la remisión.
  final String costo;
  final String lote;

  /// AAAA-MM-DD, o vacío.
  final String caducidad;

  Map<String, Object?> aJson() => {
        'sku': sku,
        'unidad': unidad,
        'cantidad': cantidad,
        'costo': costo,
        'lote': lote,
        'caducidad': caducidad,
      };
}

// ---------------------------------------------------------------------------
// Traspasos
// ---------------------------------------------------------------------------
class TraspasoEntreBodegas {
  const TraspasoEntreBodegas({
    required this.id,
    required this.folio,
    required this.creadoEn,
    required this.origen,
    required this.destino,
    required this.quien,
    required this.renglones,
    required this.piezas,
    required this.nota,
  });

  factory TraspasoEntreBodegas.deJson(Map<String, Object?> j) => TraspasoEntreBodegas(
        id: j['id']! as String,
        folio: j['folio'] as String?,
        creadoEn: DateTime.parse(j['creado_en']! as String),
        origen: j['origen']! as String,
        destino: j['destino']! as String,
        quien: j['quien'] as String?,
        renglones: (j['renglones']! as num).toInt(),
        piezas: j['piezas']! as String,
        nota: j['observaciones'] as String?,
      );

  final String id;
  final String? folio;
  final DateTime creadoEn;
  final String origen;
  final String destino;
  final String? quien;
  final int renglones;
  final String piezas;
  final String? nota;
}

class ListaDeTraspasos {
  const ListaDeTraspasos({required this.traspasos, required this.bodegas});

  factory ListaDeTraspasos.deJson(Map<String, Object?> j) => ListaDeTraspasos(
        traspasos: [
          for (final t in j['traspasos']! as List) TraspasoEntreBodegas.deJson((t as Map).cast()),
        ],
        bodegas: [
          for (final b in j['bodegas']! as List) OpcionDeAlmacen.deJson((b as Map).cast()),
        ],
      );

  final List<TraspasoEntreBodegas> traspasos;
  final List<OpcionDeAlmacen> bodegas;
}

/// Un renglón del traspaso: cuántos bultos de qué presentación.
class RenglonPorTraspasar {
  const RenglonPorTraspasar({
    required this.productoId,
    required this.unidad,
    required this.cantidad,
  });

  final String productoId;
  final String unidad;
  final String cantidad;

  Map<String, Object?> aJson() =>
      {'producto_id': productoId, 'unidad': unidad, 'cantidad': cantidad};
}

class TraspasoHecho {
  const TraspasoHecho({required this.id, required this.folio, required this.mensaje});

  factory TraspasoHecho.deJson(Map<String, Object?> j) => TraspasoHecho(
        id: j['id']! as String,
        folio: j['folio']! as String,
        mensaje: j['mensaje']! as String,
      );

  final String id;
  final String folio;
  final String mensaje;
}

// ---------------------------------------------------------------------------
// El cliente
// ---------------------------------------------------------------------------
class ClienteAlmacen {
  const ClienteAlmacen(this._transporte);

  final Transporte _transporte;

  Future<List<AlmacenResumen>> almacenes() async => [
        for (final a in await _get('/v1/almacen') as List)
          AlmacenResumen.deJson((a as Map).cast()),
      ];

  Future<ExistenciasDelAlmacen> existencias(String almacenId, {String busqueda = ''}) async =>
      ExistenciasDelAlmacen.deJson(
        (await _get(
          '/v1/almacen/$almacenId/existencias',
          parametros: {if (busqueda.trim().isNotEmpty) 'q': busqueda.trim()},
        ) as Map)
            .cast(),
      );

  /// El catálogo con lo que hay en `almacenId`. Para traspasar, solo lo que
  /// hay (`soloConExistencia`); para recibir, todo.
  Future<List<ProductoParaCapturar>> productos(
    String almacenId, {
    bool soloConExistencia = false,
  }) async =>
      [
        for (final p in await _get(
          '/v1/almacen/productos',
          parametros: {
            'almacen_id': almacenId,
            if (soloConExistencia) 'solo_con_existencia': 'true',
          },
        ) as List)
          ProductoParaCapturar.deJson((p as Map).cast()),
      ];

  Future<ListaDeEntradas> entradas() async =>
      ListaDeEntradas.deJson((await _get('/v1/almacen/entradas') as Map).cast());

  Future<DetalleDeEntrada> verEntrada(String id) async =>
      DetalleDeEntrada.deJson((await _get('/v1/almacen/entradas/$id') as Map).cast());

  Future<DetalleDeEntrada> abrirEntrada(NuevaEntrada nueva) =>
      _entrada('/v1/almacen/entradas', nueva.aJson());

  Future<DetalleDeEntrada> agregarRenglon(String id, RenglonPorRecibir renglon) =>
      _entrada('/v1/almacen/entradas/$id/renglones', renglon.aJson());

  Future<DetalleDeEntrada> quitarRenglon(String id, String renglonId) =>
      _entrada('/v1/almacen/entradas/$id/renglones/$renglonId/quitar', const {});

  Future<DetalleDeEntrada> confirmarEntrada(String id) =>
      _entrada('/v1/almacen/entradas/$id/confirmar', const {});

  Future<DetalleDeEntrada> cancelarEntrada(String id, {required String motivo}) =>
      _entrada('/v1/almacen/entradas/$id/cancelar', {'motivo': motivo});

  /// La compra que el gerente recibió en la calle (§83), de un jalón.
  ///
  /// Idempotente por `compra.id`: si ya había entrado, el servidor devuelve la
  /// misma entrada sin volver a sumar.
  Future<DetalleDeEntrada> recibirCompra(CompraCapturada compra) =>
      _entrada('/v1/almacen/compras', compra.aJson());

  /// Lo que hace falta para capturar compras sin señal: el catálogo de la
  /// bodega y los proveedores. Se guarda en el teléfono cada vez que hay señal.
  Future<CatalogoDeCompras> catalogoDeCompras() async {
    final lista = await entradas();
    final bodega = lista.bodegas.isEmpty ? null : lista.bodegas.first.id;
    final productos = bodega == null
        ? const <Object?>[]
        : await _get('/v1/almacen/productos', parametros: {'almacen_id': bodega}) as List;
    return CatalogoDeCompras(
      productos: [
        for (final p in productos) ProductoParaComprar.deJson((p! as Map).cast()),
      ],
      proveedores: lista.proveedores,
    );
  }

  Future<ListaDeTraspasos> traspasos() async =>
      ListaDeTraspasos.deJson((await _get('/v1/almacen/traspasos') as Map).cast());

  Future<TraspasoHecho> traspasar({
    required String origenId,
    required String destinoId,
    required List<RenglonPorTraspasar> renglones,
    String nota = '',
  }) async {
    final r = await _transporte.post('/v1/almacen/traspasos', {
      'origen_id': origenId,
      'destino_id': destinoId,
      'renglones': [for (final x in renglones) x.aJson()],
      'nota': nota,
    });
    _revisar(r);
    return TraspasoHecho.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  Future<Object?> _get(String ruta, {Map<String, String> parametros = const {}}) async {
    // Sin parámetros, sin `?` colgando en la URL.
    final r = parametros.isEmpty
        ? await _transporte.obtener(ruta)
        : await _transporte.obtener(ruta, parametros: parametros);
    _revisar(r);
    return jsonDecode(r.cuerpo);
  }

  Future<DetalleDeEntrada> _entrada(String ruta, Map<String, Object?> cuerpo) async {
    final r = await _transporte.post(ruta, cuerpo);
    _revisar(r);
    return DetalleDeEntrada.deJson((jsonDecode(r.cuerpo) as Map).cast());
  }

  void _revisar(RespuestaHttp r) {
    if (r.ok) return;
    if (r.codigo == 401) throw SesionInvalida(r.codigo);
    if (r.codigo == 403) throw const SinPermisoDeAlmacen();
    if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
      throw const ServidorSinEstaFuncion();
    }
    // El mismo tipo que las cargas: «el servidor dijo que no, y este es su
    // texto» («No alcanza en Bodega: …»). La pantalla lo muestra tal cual.
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
