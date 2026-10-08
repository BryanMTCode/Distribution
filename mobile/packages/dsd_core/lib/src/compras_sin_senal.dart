/// La compra a proveedor que el gerente recibe en la calle, aunque no haya
/// señal (ADR 0002 §83).
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ ES Y QUÉ NO ES
/// ─────────────────────────────────────────────────────────────────────────
/// «Agregar producto» en el teléfono del gerente NO es dar de alta un artículo
/// en el catálogo: es registrar que llegó mercancía de un proveedor —qué
/// productos y cuántos— para que sume al inventario de la bodega principal.
///
/// ─────────────────────────────────────────────────────────────────────────
/// SIN SEÑAL: SE GUARDA PRIMERO, SE MANDA DESPUÉS
/// ─────────────────────────────────────────────────────────────────────────
/// La compra se guarda aquí con el id que le da el teléfono y se manda ENTERA al
/// tener señal. El servidor la reconoce por ese id, así que reintentar —porque
/// la señal se fue a la mitad, porque la respuesta no llegó— no la suma dos
/// veces. Lo que suma a la bodega es una cantidad (un delta), nunca un «la
/// bodega tiene tanto».
///
/// Para capturar sin señal hace falta el catálogo: se guarda una copia cada vez
/// que hay señal, con su fecha.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL COSTO ES OPCIONAL
/// ─────────────────────────────────────────────────────────────────────────
/// Por renglón. Con costo: mueve el costo promedio y entra a la cuenta por
/// pagar del proveedor. Sin costo: solo suma inventario.
library;

import 'dart:convert';

import 'package:sqlite3/sqlite3.dart';

import 'almacen_cliente.dart';
import 'cargas_cliente.dart' show CargaRechazada;
import 'dia_operativo.dart';
import 'dinero.dart';
import 'precio.dart';
import 'sync_cliente.dart' show SesionInvalida;
import 'transporte.dart';

/// Un producto que se puede recibir, con sus presentaciones.
class ProductoParaComprar {
  const ProductoParaComprar({
    required this.productoId,
    required this.sku,
    required this.nombre,
    required this.unidadBase,
    required this.presentaciones,
  });

  factory ProductoParaComprar.deJson(Map<String, Object?> j) => ProductoParaComprar(
        productoId: j['producto_id']! as String,
        sku: j['sku']! as String,
        nombre: j['nombre']! as String,
        unidadBase: j['unidad_base']! as String,
        presentaciones: [
          for (final p in (j['presentaciones'] as List?) ?? const [])
            (
              (p as Map)['unidad']! as String,
              Factor.deBase(num.parse(p['factor']!.toString())),
            ),
        ],
      );

  final String productoId;
  final String sku;
  final String nombre;
  final String unidadBase;

  /// (unidad, factor). Se recibe por la más grande: así llega del proveedor.
  final List<(String, Factor)> presentaciones;

  (String, Factor) get porOmision {
    if (presentaciones.isEmpty) return (unidadBase, Factor.uno);
    var mejor = presentaciones.first;
    for (final p in presentaciones) {
      if (p.$2.compareTo(mejor.$2) > 0) mejor = p;
    }
    return mejor;
  }

  Map<String, Object?> aJson() => {
        'producto_id': productoId,
        'sku': sku,
        'nombre': nombre,
        'unidad_base': unidadBase,
        'presentaciones': [
          for (final (u, f) in presentaciones) {'unidad': u, 'factor': f.texto},
        ],
      };
}

/// Lo que hace falta para capturar sin señal: productos y proveedores.
class CatalogoDeCompras {
  const CatalogoDeCompras({required this.productos, required this.proveedores});

  factory CatalogoDeCompras.deJson(Map<String, Object?> j) => CatalogoDeCompras(
        productos: [
          for (final p in (j['productos'] as List?) ?? const [])
            ProductoParaComprar.deJson((p as Map).cast()),
        ],
        proveedores: [
          for (final p in (j['proveedores'] as List?) ?? const [])
            OpcionDeAlmacen.deJson((p as Map).cast()),
        ],
      );

  final List<ProductoParaComprar> productos;
  final List<OpcionDeAlmacen> proveedores;

  Map<String, Object?> aJson() => {
        'productos': [for (final p in productos) p.aJson()],
        'proveedores': [
          for (final p in proveedores) {'id': p.id, 'codigo': p.codigo, 'nombre': p.nombre},
        ],
      };
}

/// Un renglón de la compra: cuántos bultos llegaron y, si se sabe, a cuánto.
class RenglonDeCompra {
  const RenglonDeCompra({
    required this.productoId,
    required this.nombre,
    required this.unidad,
    required this.factor,
    required this.bultos,
    this.costoPorBulto,
  });

  factory RenglonDeCompra.deJson(Map<String, Object?> j) => RenglonDeCompra(
        productoId: j['producto_id']! as String,
        nombre: j['nombre'] as String? ?? '',
        unidad: j['unidad']! as String,
        factor: Factor.deTexto(j['factor'] as String? ?? '1.0000'),
        bultos: int.parse(j['cantidad']! as String),
        costoPorBulto: (j['costo'] as String? ?? '').isEmpty
            ? null
            : Dinero.deTexto(j['costo']! as String),
      );

  final String productoId;
  final String nombre;
  final String unidad;
  final Factor factor;
  final int bultos;

  /// Nulo: este renglón solo suma inventario.
  final Dinero? costoPorBulto;

  Cantidad get piezas => cantidadBase(Cantidad.deEnteros(bultos), factor);

  Map<String, Object?> aJson() => {
        'producto_id': productoId,
        'nombre': nombre,
        'unidad': unidad,
        'factor': factor.texto,
        // Texto: el servidor lo valida con las reglas del panel.
        'cantidad': '$bultos',
        'costo': costoPorBulto?.texto ?? '',
      };
}

/// La compra tal como la capturó el gerente.
class CompraCapturada {
  const CompraCapturada({
    required this.id,
    required this.fecha,
    required this.renglones,
    this.proveedorId,
    this.proveedor = '',
    this.referencia = '',
    this.nota = '',
  });

  factory CompraCapturada.deJson(Map<String, Object?> j) => CompraCapturada(
        id: j['id']! as String,
        fecha: j['fecha']! as String,
        proveedorId: j['proveedor_id'] as String?,
        proveedor: j['proveedor'] as String? ?? '',
        referencia: j['referencia'] as String? ?? '',
        nota: j['nota'] as String? ?? '',
        renglones: [
          for (final r in (j['renglones'] as List?) ?? const [])
            RenglonDeCompra.deJson((r as Map).cast()),
        ],
      );

  final String id;

  /// El día en que llegó la mercancía, aunque se mande después.
  final String fecha;
  final String? proveedorId;
  final String proveedor;
  final String referencia;
  final String nota;
  final List<RenglonDeCompra> renglones;

  /// Lo que se le debe al proveedor por los renglones con costo. En centavos
  /// enteros: bultos × costo por bulto, sin pasar por `double`.
  Dinero get importeConCosto {
    var centavos = 0;
    for (final r in renglones) {
      final costo = r.costoPorBulto;
      if (costo != null) centavos += costo.centavos * r.bultos;
    }
    final signo = centavos < 0 ? '-' : '';
    final a = centavos.abs();
    return Dinero.deTexto('$signo${a ~/ 100}.${(a % 100).toString().padLeft(2, '0')}');
  }

  int get sinCosto => renglones.where((r) => r.costoPorBulto == null).length;

  Map<String, Object?> aJson() => {
        'id': id,
        'proveedor_id': proveedorId,
        'proveedor': proveedor,
        'referencia': referencia,
        'nota': nota,
        'fecha': fecha,
        'renglones': [for (final r in renglones) r.aJson()],
      };
}

/// Una compra de la cola, con lo que ha pasado con ella.
class CompraEnCola {
  const CompraEnCola({
    required this.compra,
    required this.estado,
    required this.creadaEn,
    this.folio,
    this.mensaje,
    this.enviadaEn,
  });

  final CompraCapturada compra;

  /// 'pendiente' | 'enviada' | 'rechazada'
  final String estado;
  final String creadaEn;
  final String? folio;
  final String? mensaje;
  final String? enviadaEn;

  bool get pendiente => estado == 'pendiente';
  bool get rechazada => estado == 'rechazada';
}

class ResultadoDeEnvio {
  const ResultadoDeEnvio({
    this.enviadas = 0,
    this.rechazadas = 0,
    this.sinSenal = false,
    this.ultimoMensaje,
  });

  final int enviadas;
  final int rechazadas;

  /// Se cortó por falta de señal: lo que no se mandó sigue en la cola.
  final bool sinSenal;
  final String? ultimoMensaje;
}

class MotivoNoCompra implements Exception {
  const MotivoNoCompra(this.mensaje);

  final String mensaje;

  @override
  String toString() => mensaje;
}

class ComprasSinSenal {
  ComprasSinSenal(
    this._db, {
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
  })  : _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;

  static const _claveCatalogo = 'compras:catalogo';

  // -------------------------------------------------------------------------
  // El catálogo para capturar sin señal
  // -------------------------------------------------------------------------

  void guardarCatalogo(CatalogoDeCompras catalogo) {
    _db.execute(
      'INSERT INTO tablero_cache (clave, cuerpo, recibido_en) VALUES (?, ?, ?) '
      'ON CONFLICT(clave) DO UPDATE SET cuerpo = excluded.cuerpo, '
      '                                recibido_en = excluded.recibido_en',
      [_claveCatalogo, jsonEncode(catalogo.aJson()), _ahora().toUtc().toIso8601String()],
    );
  }

  /// La copia guardada y cuándo se bajó. Nulo si nunca hubo señal.
  (CatalogoDeCompras, DateTime)? catalogoGuardado() {
    final filas = _db.select(
      'SELECT cuerpo, recibido_en FROM tablero_cache WHERE clave = ?',
      [_claveCatalogo],
    );
    if (filas.isEmpty) return null;
    try {
      return (
        CatalogoDeCompras.deJson(
          (jsonDecode(filas.single['cuerpo'] as String) as Map).cast(),
        ),
        DateTime.parse(filas.single['recibido_en'] as String).toLocal(),
      );
    } on Object {
      return null;
    }
  }

  // -------------------------------------------------------------------------
  // La cola
  // -------------------------------------------------------------------------

  /// Guarda la compra para mandarla. Lo único que se valida aquí es lo que el
  /// servidor rechazaría seguro; lo demás lo dice él al recibirla.
  CompraEnCola capturar({
    required List<RenglonDeCompra> renglones,
    String? proveedorId,
    String proveedor = '',
    String referencia = '',
    String nota = '',
  }) {
    final validos = renglones.where((r) => r.bultos > 0).toList();
    if (validos.isEmpty) {
      throw const MotivoNoCompra('Agrega al menos un producto con cuántos bultos llegaron.');
    }
    for (final r in validos) {
      final costo = r.costoPorBulto;
      if (costo != null && (costo.esCero || costo.esNegativo)) {
        throw MotivoNoCompra(
          '${r.nombre}: un costo de cero arrastraría el promedio. Déjalo vacío si '
          'no lo sabes.',
        );
      }
    }
    final instante = _ahora();
    final compra = CompraCapturada(
      id: _nuevoUuid(),
      fecha: diaOperativoDe(instante),
      proveedorId: proveedorId,
      proveedor: proveedor.trim(),
      referencia: referencia.trim(),
      nota: nota.trim(),
      renglones: validos,
    );
    final creada = instante.toUtc().toIso8601String();
    _db.execute(
      "INSERT INTO compras_pendientes (id, payload, estado, creada_en) "
      "VALUES (?, ?, 'pendiente', ?)",
      [compra.id, jsonEncode(compra.aJson()), creada],
    );
    return CompraEnCola(compra: compra, estado: 'pendiente', creadaEn: creada);
  }

  List<CompraEnCola> todas({int limite = 50}) => _db
      .select(
        'SELECT * FROM compras_pendientes ORDER BY creada_en DESC, rowid DESC LIMIT ?',
        [limite],
      )
      .map(_aCompra)
      .toList();

  int get pendientes => _db
      .select("SELECT COUNT(*) AS n FROM compras_pendientes WHERE estado = 'pendiente'")
      .single['n'] as int;

  CompraEnCola _aCompra(Row f) => CompraEnCola(
        compra: CompraCapturada.deJson(
          (jsonDecode(f['payload'] as String) as Map).cast(),
        ),
        estado: f['estado'] as String,
        creadaEn: f['creada_en'] as String,
        folio: f['folio'] as String?,
        mensaje: f['mensaje'] as String?,
        enviadaEn: f['enviada_en'] as String?,
      );

  /// Vuelve a intentar una que el servidor rechazó (después de arreglar lo
  /// que dijo, del lado del servidor: un proveedor, un producto).
  void reintentar(String id) => _db.execute(
        "UPDATE compras_pendientes SET estado = 'pendiente' WHERE id = ? AND estado = 'rechazada'",
        [id],
      );

  /// Se olvida de una rechazada. Una enviada no se borra: es el comprobante.
  void descartar(String id) => _db.execute(
        "DELETE FROM compras_pendientes WHERE id = ? AND estado = 'rechazada'",
        [id],
      );

  /// Manda las pendientes, la más vieja primero.
  ///
  /// Sin señal se detiene y no marca nada: la siguiente vez se reintenta. Un
  /// rechazo del servidor (un 409: «media caja», «ese proveedor ya no existe»)
  /// se guarda con su texto y no se reintenta solo: reintentarlo igual daría lo
  /// mismo. Una sesión vencida se propaga: que la pantalla mande a entrar.
  Future<ResultadoDeEnvio> enviar(ClienteAlmacen cliente) async {
    final pendientes = _db
        .select(
          "SELECT * FROM compras_pendientes WHERE estado = 'pendiente' ORDER BY creada_en, rowid",
        )
        .map(_aCompra)
        .toList();
    var enviadas = 0;
    var rechazadas = 0;
    String? ultimo;
    for (final p in pendientes) {
      try {
        final entrada = await cliente.recibirCompra(p.compra);
        _db.execute(
          "UPDATE compras_pendientes SET estado = 'enviada', folio = ?, mensaje = ?, "
          "       enviada_en = ? WHERE id = ?",
          [entrada.folio, entrada.mensaje, _ahora().toUtc().toIso8601String(), p.compra.id],
        );
        enviadas++;
        ultimo = entrada.mensaje;
      } on ErrorDeRed {
        return ResultadoDeEnvio(
          enviadas: enviadas,
          rechazadas: rechazadas,
          sinSenal: true,
          ultimoMensaje: ultimo,
        );
      } on SesionInvalida {
        rethrow;
      } on CargaRechazada catch (e) {
        _db.execute(
          "UPDATE compras_pendientes SET estado = 'rechazada', mensaje = ? WHERE id = ?",
          [e.detalle, p.compra.id],
        );
        rechazadas++;
        ultimo = e.detalle;
      }
    }
    return ResultadoDeEnvio(enviadas: enviadas, rechazadas: rechazadas, ultimoMensaje: ultimo);
  }
}
