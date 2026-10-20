/// Reimprimir el ticket de una venta desde «Mi día» (ADR 0002 §159).
///
/// ───────────────────────────────────────────────────────────────────────────
/// QUÉ SALE EN EL PAPEL
/// ───────────────────────────────────────────────────────────────────────────
/// · **Ya se había impreso:** los bytes del original, guardados en
///   `ventas.ticket_escpos`, con el aviso «COPIA n» encima. Es la misma regla que
///   la copia que se pide al cobrar: el papel dice exactamente lo mismo que el
///   que recibió el cliente.
/// · **Nunca se imprimió** —la impresora no tenía papel, o el vendedor no la
///   pidió—: se arma desde la base y sale como ORIGINAL, y desde ahí se guarda.
/// · **La oficina la corrigió:** el original guardado dice otras cantidades. Se
///   arma como quedó, con «CORREGIDA POR LA OFICINA», y como copia si ya había
///   salido una.
/// · **El vendedor la modificó (§162):** sale armada de nuevo, como original de la
///   venta modificada, con «MODIFICADA · Sustituye al ticket anterior».
/// · **La cancelada no se reimprime:** un papel de una venta que ya no existe es
///   un cobro que no se debe.
///
/// Todo sale de SQLite: se reimprime sin señal.
library;

import 'package:sqlite3/sqlite3.dart';

import 'carrito.dart';
import 'dinero.dart';
import 'escpos.dart';
import 'forma_de_pago.dart';
import 'precio.dart';
import 'ticket.dart';
import 'venta.dart';

/// Lo que se manda a la impresora, y lo que se anota al salir.
class TicketParaReimprimir {
  const TicketParaReimprimir({
    required this.bytes,
    required this.original,
    required this.copia,
    this.corregida = false,
  });

  /// Lo que va a la impresora.
  final List<int> bytes;

  /// El ticket sin el aviso de copia: es lo que se guarda si es la primera vez
  /// que sale.
  final List<int> original;

  /// 0 si sale como original; 1 en adelante, el número de copia.
  final int copia;

  /// Si se armó con lo que dejó la corrección de la oficina.
  final bool corregida;

  bool get esCopia => copia > 0;
}

class ReimpresionDeVentas {
  ReimpresionDeVentas(this._db);

  final Database _db;

  /// El ticket de [ventaId] para volver a imprimirlo, o nulo si no existe o está
  /// cancelada.
  TicketParaReimprimir? ticketDe(
    String ventaId, {
    required DatosDelNegocio negocio,
    required String nombreVendedor,
    int columnas = columnas58mm,
    TablaDeCodigos tabla = TablaDeCodigos.pc437,
  }) {
    final filas = _db.select(
      '''
      SELECT v.*, c.nombre_comercial AS cliente_nombre, c.codigo AS cliente_codigo,
             c.direccion AS cliente_direccion
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
       WHERE v.id = ?
      ''',
      [ventaId],
    );
    if (filas.isEmpty) return null;
    final v = filas.single;
    if (v['estado'] != 'confirmada') return null;

    final impresa = (v['impreso'] as int? ?? 0) == 1;
    final copia = impresa ? (v['reimpresiones'] as int? ?? 0) + 1 : 0;
    final corregida = v['nota_oficina'] != null;
    // Al modificarla (§162) el teléfono olvida el ticket de antes: el siguiente
    // sale armado de nuevo, como original de la venta modificada, y lo dice.
    final modificada = (v['modificaciones'] as int? ?? 0) > 0;
    final guardado = v['ticket_escpos'];

    if (impresa && !corregida && guardado is List<int>) {
      final original = List<int>.of(guardado);
      return TicketParaReimprimir(
        bytes: reimpresion(original, copia, columnas: columnas, tabla: tabla),
        original: original,
        copia: copia,
      );
    }

    final original = ticketDeVenta(
      _venta(v),
      negocio: negocio,
      visita: DatosDeLaVisita(
        nombreCliente: v['cliente_nombre'] as String? ?? 'Cliente',
        nombreVendedor: nombreVendedor,
        codigoCliente: v['cliente_codigo'] as String?,
        direccionCliente: v['cliente_direccion'] as String?,
      ),
      columnas: columnas,
      tabla: tabla,
      corregida: corregida,
      modificada: modificada,
    );
    return TicketParaReimprimir(
      bytes: copia == 0
          ? original
          : reimpresion(original, copia, columnas: columnas, tabla: tabla),
      original: original,
      copia: copia,
      corregida: corregida,
    );
  }

  /// Ya salió en papel: la primera vez queda como impresa con su original; las
  /// siguientes cuentan como reimpresión.
  void anotarImpresa(String ventaId, TicketParaReimprimir ticket) =>
      marcarVentaImpresa(_db, ventaId, ticket: ticket.original);

  /// La venta como la tiene la base, con sus renglones en el orden del ticket.
  VentaGuardada _venta(Row v) {
    final lineas = _db
        .select(
          '''
          SELECT vp.producto_id, vp.unidad_codigo, vp.factor_unidad, vp.cantidad,
                 vp.precio_unitario, vp.tasa_iva,
                 COALESCE(p.nombre, vp.producto_id) AS nombre,
                 COALESCE(p.sku, '') AS sku, COALESCE(p.unidad_base, 'PZA') AS base
            FROM venta_partidas vp
            LEFT JOIN productos p ON p.id = vp.producto_id
           WHERE vp.venta_id = ?
           ORDER BY vp.linea
          ''',
          [v['id']],
        )
        .map(
          (f) => LineaCarrito(
            cantidad: Cantidad.deBase(f['cantidad'] as num),
            presentacion: PresentacionVendible(
              productoId: f['producto_id'] as String,
              sku: f['sku'] as String,
              nombre: f['nombre'] as String,
              unidadCodigo: f['unidad_codigo'] as String,
              unidadBaseCodigo: f['base'] as String,
              factor: Factor.deBase(f['factor_unidad'] as num),
              precio: Precio.deBase(f['precio_unitario'] as num),
              listaPreciosId: v['lista_precios_id'] as String? ?? '',
              listaPreciosVersion: v['lista_precios_version'] as int? ?? 0,
              tasaIva: (f['tasa_iva'] as num?)?.toDouble() ?? 0,
            ),
          ),
        )
        .toList();

    return VentaGuardada(
      id: v['id'] as String,
      folioConsecutivo: v['folio_consecutivo'] as int,
      folioLocal: v['folio_local'] as String,
      visitaId: v['visita_id'] as String? ?? '',
      clienteId: v['cliente_id'] as String,
      formaDePago: FormaDePago.deCodigo(v['forma_pago'] as String?),
      referenciaPago: v['referencia_pago'] as String?,
      subtotal: _dinero(v['subtotal']),
      total: _dinero(v['total']),
      lineas: lineas,
      fechaDispositivo: v['fecha_dispositivo'] as String,
      fechaOperativa: v['fecha_operativa'] as String,
      foliosRestantes: 0,
    );
  }
}

/// Dinero desde una columna REAL de SQLite, redondeado una vez en el borde (ver
/// `mi_dia.dart`).
Dinero _dinero(Object? valor) =>
    Dinero.deTexto(((valor as num?) ?? 0).toDouble().toStringAsFixed(2));
