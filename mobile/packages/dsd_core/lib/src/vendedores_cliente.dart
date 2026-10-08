/// Los vendedores, para el portal de la oficina: el cliente de `/v1/vendedores`.
///
/// Solo lo usa quien tiene `ventas.ver_todas` (admin, gerente, supervisor). Las
/// cifras salen de las mismas consultas que la pantalla Vendedores del panel, así
/// que el teléfono y el dashboard dicen lo mismo.
///
/// Necesita red, como el tablero. Los importes llegan como texto con dos
/// decimales y se convierten a `Dinero`; las cantidades se quedan como texto
/// porque esta pantalla solo las muestra.
library;

import 'dart:convert';

import 'dia_operativo.dart';
import 'dinero.dart';
import 'forma_de_pago.dart';
import 'sync_cliente.dart' show SesionInvalida, ServidorConProblemas;
import 'transporte.dart';

/// Sin `ventas.ver_todas`.
class SinPermisoDeVendedores implements Exception {
  const SinPermisoDeVendedores();
}

/// El servidor no tiene esta ruta: todavía no se actualizó.
class ServidorSinEstaFuncion implements Exception {
  const ServidorSinEstaFuncion();
}

class PeriodoVisto {
  const PeriodoVisto({
    required this.clave,
    required this.etiqueta,
    required this.desde,
    required this.hasta,
    required this.descripcion,
  });

  factory PeriodoVisto.deJson(Map<String, Object?> j) => PeriodoVisto(
        clave: j['clave']! as String,
        etiqueta: j['etiqueta']! as String,
        desde: j['desde']! as String,
        hasta: j['hasta']! as String,
        descripcion: j['descripcion']! as String,
      );

  final String clave;
  final String etiqueta;

  /// `YYYY-MM-DD`, los dos incluidos.
  final String desde;
  final String hasta;
  final String descripcion;

  /// «Hoy, martes 7 de octubre» o «del lunes 6 de octubre al domingo 12 de
  /// octubre»: los días de las cifras, siempre a la vista.
  String enPalabras({required String hoy}) => desde == hasta
      ? encabezadoDelDia(desde, hoy: hoy)
      : 'Del ${diaEnPalabras(desde)} al ${diaEnPalabras(hasta)}';
}

class VendedorEnLista {
  const VendedorEnLista({
    required this.id,
    required this.codigo,
    required this.nombre,
    required this.activo,
    required this.camion,
    required this.rutas,
    required this.ventas,
    required this.importe,
    required this.efectivo,
    required this.noVentas,
    required this.mermas,
    required this.ultimoContacto,
    required this.saldoCuenta,
  });

  factory VendedorEnLista.deJson(Map<String, Object?> j) => VendedorEnLista(
        id: j['id']! as String,
        codigo: j['codigo']! as String,
        nombre: j['nombre']! as String,
        activo: j['activo']! as bool,
        camion: j['camion'] as String?,
        rutas: j['rutas'] as String?,
        ventas: (j['ventas']! as num).toInt(),
        importe: Dinero.deTexto(j['importe']! as String),
        efectivo: Dinero.deTexto(j['efectivo']! as String),
        noVentas: (j['no_ventas']! as num).toInt(),
        mermas: (j['mermas']! as num).toInt(),
        ultimoContacto: _fecha(j['ultimo_contacto']),
        saldoCuenta: Dinero.deTexto(j['saldo_cuenta']! as String),
      );

  final String id;
  final String codigo;
  final String nombre;
  final bool activo;
  final String? camion;
  final String? rutas;
  final int ventas;
  final Dinero importe;
  /// Lo vendido en efectivo: lo que entrega en el corte.
  final Dinero efectivo;
  final int noVentas;
  final int mermas;
  final DateTime? ultimoContacto;

  /// Lo que debe en su cuenta (faltantes, cargos). Positivo = debe.
  final Dinero saldoCuenta;
}

class ListaDeVendedores {
  const ListaDeVendedores({
    required this.periodo,
    required this.periodos,
    required this.vendedores,
  });

  factory ListaDeVendedores.deJson(Map<String, Object?> j) => ListaDeVendedores(
        periodo: PeriodoVisto.deJson((j['periodo']! as Map).cast()),
        periodos: _periodos(j['periodos']),
        vendedores: _lista(j['vendedores'], VendedorEnLista.deJson),
      );

  final PeriodoVisto periodo;

  /// `(clave, etiqueta)`, en el orden del selector.
  final List<(String, String)> periodos;
  final List<VendedorEnLista> vendedores;
}

class ResumenDeTipo {
  const ResumenDeTipo({
    required this.tipo,
    required this.etiqueta,
    required this.cuantos,
    required this.importe,
  });

  factory ResumenDeTipo.deJson(Map<String, Object?> j) => ResumenDeTipo(
        tipo: j['tipo']! as String,
        etiqueta: j['etiqueta']! as String,
        cuantos: (j['cuantos']! as num).toInt(),
        importe: Dinero.deTexto(j['importe']! as String),
      );

  final String tipo;
  final String etiqueta;
  final int cuantos;
  final Dinero importe;
}

class MovimientoDeVendedor {
  const MovimientoDeVendedor({
    required this.momento,
    required this.fecha,
    required this.tipo,
    required this.etiqueta,
    required this.folio,
    required this.cliente,
    required this.detalle,
    required this.importe,
    required this.estado,
    required this.marca,
    required this.ref,
  });

  factory MovimientoDeVendedor.deJson(Map<String, Object?> j) => MovimientoDeVendedor(
        momento: _fecha(j['momento']),
        fecha: j['fecha'] as String?,
        tipo: j['tipo']! as String,
        etiqueta: j['etiqueta']! as String,
        folio: j['folio'] as String?,
        cliente: j['cliente'] as String?,
        detalle: j['detalle'] as String?,
        importe: j['importe'] == null ? null : Dinero.deTexto(j['importe']! as String),
        estado: j['estado'] as String?,
        marca: j['marca'] as bool? ?? false,
        ref: j['ref'] as String?,
      );

  final DateTime? momento;
  final String? fecha;
  final String tipo;
  final String etiqueta;
  final String? folio;
  final String? cliente;
  final String? detalle;
  final Dinero? importe;
  final String? estado;

  /// Algo que revisar: una venta marcada, una carga forzada, un corte con diferencia.
  final bool marca;

  /// El id del documento. Con él se abre el detalle de una venta.
  final String? ref;
}

class TelefonoDeVendedor {
  const TelefonoDeVendedor({
    required this.etiqueta,
    required this.estado,
    required this.ultimaSubida,
    required this.ultimaBajada,
    required this.colaPendiente,
  });

  factory TelefonoDeVendedor.deJson(Map<String, Object?> j) => TelefonoDeVendedor(
        etiqueta: j['etiqueta']! as String,
        estado: j['estado']! as String,
        ultimaSubida: _fecha(j['ultima_sync_push_en']),
        ultimaBajada: _fecha(j['ultima_sync_pull_en']),
        colaPendiente: (j['cola_pendiente'] as num?)?.toInt(),
      );

  final String etiqueta;
  final String estado;
  final DateTime? ultimaSubida;
  final DateTime? ultimaBajada;
  final int? colaPendiente;
}

class DetalleDeVendedor {
  const DetalleDeVendedor({
    required this.id,
    required this.codigo,
    required this.nombre,
    required this.activo,
    required this.camion,
    required this.rutas,
    required this.saldoCuenta,
    required this.periodo,
    required this.periodos,
    required this.resumen,
    required this.movimientos,
    required this.recortado,
    required this.limite,
    required this.telefonos,
  });

  factory DetalleDeVendedor.deJson(Map<String, Object?> j) {
    final v = (j['vendedor']! as Map).cast<String, Object?>();
    return DetalleDeVendedor(
      id: v['id']! as String,
      codigo: v['codigo']! as String,
      nombre: v['nombre']! as String,
      activo: v['activo']! as bool,
      camion: v['camion'] as String?,
      rutas: v['rutas'] as String?,
      saldoCuenta: Dinero.deTexto(v['saldo_cuenta']! as String),
      periodo: PeriodoVisto.deJson((j['periodo']! as Map).cast()),
      periodos: _periodos(j['periodos']),
      resumen: _lista(j['resumen'], ResumenDeTipo.deJson),
      movimientos: _lista(j['movimientos'], MovimientoDeVendedor.deJson),
      recortado: j['recortado']! as bool,
      limite: (j['limite']! as num).toInt(),
      telefonos: _lista(j['telefonos'], TelefonoDeVendedor.deJson),
    );
  }

  final String id;
  final String codigo;
  final String nombre;
  final bool activo;
  final String? camion;
  final String? rutas;
  final Dinero saldoCuenta;
  final PeriodoVisto periodo;
  final List<(String, String)> periodos;
  final List<ResumenDeTipo> resumen;
  final List<MovimientoDeVendedor> movimientos;
  final bool recortado;
  final int limite;
  final List<TelefonoDeVendedor> telefonos;
}

class ExistenciaEnCamion {
  const ExistenciaEnCamion({
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.cantidad,
  });

  factory ExistenciaEnCamion.deJson(Map<String, Object?> j) => ExistenciaEnCamion(
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        cantidad: j['cantidad']! as String,
      );

  final String sku;
  final String nombre;
  final String unidadBase;
  final String cantidad;

  bool get negativa => cantidad.startsWith('-');
}

class CamionDelVendedor {
  const CamionDelVendedor({
    required this.vendedor,
    required this.camion,
    required this.existencias,
    required this.piezas,
    required this.ultimoContacto,
  });

  factory CamionDelVendedor.deJson(Map<String, Object?> j) => CamionDelVendedor(
        vendedor: j['vendedor']! as String,
        camion: j['camion'] as String?,
        existencias: _lista(j['existencias'], ExistenciaEnCamion.deJson),
        piezas: j['piezas']! as String,
        ultimoContacto: _fecha(j['ultimo_contacto']),
      );

  final String vendedor;
  final String? camion;
  final List<ExistenciaEnCamion> existencias;
  final String piezas;

  /// Lo que vendió después de esta hora, sin señal, todavía no está restado.
  final DateTime? ultimoContacto;
}

class PartidaVista {
  const PartidaVista({
    required this.nombre,
    required this.unidad,
    required this.cantidad,
    required this.precioUnitario,
    required this.importe,
  });

  factory PartidaVista.deJson(Map<String, Object?> j) => PartidaVista(
        nombre: j['nombre']! as String,
        unidad: j['unidad']! as String,
        cantidad: j['cantidad']! as String,
        precioUnitario: Dinero.deTexto(j['precio_unitario']! as String),
        importe: Dinero.deTexto(j['importe']! as String),
      );

  final String nombre;
  final String unidad;
  final String cantidad;
  final Dinero precioUnitario;
  final Dinero importe;
}

class VentaVista {
  const VentaVista({
    required this.folio,
    required this.fecha,
    required this.momento,
    required this.vendedor,
    required this.cliente,
    required this.tipo,
    required this.estado,
    required this.total,
    required this.partidas,
    this.formaDePago,
    this.pagoEstado,
  });

  factory VentaVista.deJson(Map<String, Object?> j) => VentaVista(
        folio: j['folio'] as String?,
        fecha: j['fecha_operativa']! as String,
        momento: _fecha(j['momento']),
        vendedor: j['vendedor']! as String,
        cliente: j['cliente']! as String,
        tipo: j['tipo']! as String,
        estado: j['estado']! as String,
        total: Dinero.deTexto(j['total']! as String),
        partidas: _lista(j['partidas'], PartidaVista.deJson),
        formaDePago: j['forma_pago'] == null
            ? null
            : FormaDePago.deCodigo(j['forma_pago'] as String?),
        pagoEstado: j['pago_estado'] as String?,
      );

  final String? folio;
  final String fecha;
  final DateTime? momento;
  final String vendedor;
  final String cliente;
  final String tipo;

  /// Nula en las ventas a crédito del piloto y con un servidor anterior.
  final FormaDePago? formaDePago;

  /// 'confirmado' | 'por_confirmar' | 'rechazado'.
  final String? pagoEstado;
  final String estado;
  final Dinero total;
  final List<PartidaVista> partidas;
}

class ClienteVendedores {
  const ClienteVendedores(this._transporte);

  final Transporte _transporte;

  /// Con `desde` y `hasta` (`YYYY-MM-DD`) es un rango a mano; si no, el periodo
  /// con nombre.
  Future<ListaDeVendedores> lista({
    String periodo = 'hoy',
    String? desde,
    String? hasta,
  }) async =>
      ListaDeVendedores.deJson(
        await _get('/v1/vendedores', _periodo(periodo, desde, hasta)),
      );

  Future<DetalleDeVendedor> detalle(
    String id, {
    String periodo = 'hoy',
    String? desde,
    String? hasta,
    String tipo = '',
  }) async =>
      DetalleDeVendedor.deJson(await _get('/v1/vendedores/$id', {
        ..._periodo(periodo, desde, hasta),
        if (tipo.isNotEmpty) 'tipo': tipo,
      }));

  Map<String, String> _periodo(String periodo, String? desde, String? hasta) =>
      desde != null && hasta != null
          ? {'periodo': 'rango', 'desde': desde, 'hasta': hasta}
          : {'periodo': periodo};

  Future<CamionDelVendedor> camion(String id) async =>
      CamionDelVendedor.deJson(await _get('/v1/vendedores/$id/camion'));

  Future<VentaVista> venta(String id) async =>
      VentaVista.deJson(await _get('/v1/vendedores/ventas/$id'));

  Future<Map<String, Object?>> _get(String ruta, [Map<String, String>? parametros]) async {
    final r = parametros == null
        ? await _transporte.obtener(ruta)
        : await _transporte.obtener(ruta, parametros: parametros);
    if (!r.ok) {
      if (r.codigo == 401) throw SesionInvalida(r.codigo);
      if (r.codigo == 403) throw const SinPermisoDeVendedores();
      // FastAPI contesta «Not Found» cuando la RUTA no existe: el servidor es
      // más viejo que la app. Un 404 con otro texto es que el vendedor o la
      // venta no existen, y eso lo dice el servidor.
      if (r.codigo == 404 && r.cuerpo.contains('"Not Found"')) {
        throw const ServidorSinEstaFuncion();
      }
      throw ServidorConProblemas(r.codigo, r.cuerpo);
    }
    return (jsonDecode(r.cuerpo) as Map).cast<String, Object?>();
  }
}

DateTime? _fecha(Object? crudo) => crudo == null ? null : DateTime.parse(crudo as String);

List<(String, String)> _periodos(Object? crudo) => [
      for (final p in (crudo ?? const <Object?>[]) as List)
        ((p as List)[0] as String, p[1] as String),
    ];

List<T> _lista<T>(Object? crudo, T Function(Map<String, Object?>) de) => [
      for (final e in (crudo ?? const <Object?>[]) as List)
        de((e as Map).cast<String, Object?>()),
    ];
