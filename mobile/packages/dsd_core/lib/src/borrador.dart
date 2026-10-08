/// El carrito a medio armar, sobreviviendo a que el sistema mate la app.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTO NO ES UN LUJO
/// ─────────────────────────────────────────────────────────────────────────
/// El carrito no es un documento: no tiene folio, no descuenta inventario y no
/// viaja al servidor. Pero perderlo duele igual.
///
/// Los vendedores usan Android de gama baja (ADR 0002 §5). Veinte minutos en un
/// mercado con la app en segundo plano —una llamada, WhatsApp, la cámara—
/// alcanzan para que el sistema la mate. Sin esto, el vendedor vuelve a una
/// pantalla en blanco y tiene que rearmar quince renglones frente al cliente. En
/// la práctica no los rearma: los apunta en papel, y deja de usar la app.
///
/// ─────────────────────────────────────────────────────────────────────────
/// SE GUARDA EL PRECIO, NO SOLO EL PRODUCTO
/// ─────────────────────────────────────────────────────────────────────────
/// El borrador congela la presentación completa, con su precio. Si un pull
/// refresca el catálogo a media visita, el vendedor sigue viendo **lo que le
/// cotizó al cliente**. Volver a resolver los precios al restaurar cambiaría los
/// números en la pantalla sin que nadie los tocara, justo mientras el cliente
/// los está mirando.
///
/// La existencia del camión sí se revalida al confirmar: esa no se congela,
/// porque el camión es físico y lo que ya no está no se puede entregar.
library;

import 'dart:convert';

import 'package:sqlite3/sqlite3.dart';

import 'carrito.dart';
import 'forma_de_pago.dart';
import 'precio.dart';

class BorradorDeCarrito {
  const BorradorDeCarrito({required this.clienteId, required this.carrito});

  final String clienteId;
  final Carrito carrito;
}

class RepoBorrador {
  const RepoBorrador(this._db);

  final Database _db;

  /// Guarda el carrito de la visita. Reemplaza el anterior: una visita a la vez.
  void guardar(
    String clienteId,
    Carrito carrito, {
    required String ahora,
  }) {
    if (carrito.estaVacio) {
      limpiar();
      return;
    }
    _db.execute(
      '''
      INSERT INTO carrito_borrador (id, cliente_id, forma_pago, referencia_pago,
                                    lineas_json, actualizado_en)
      VALUES (1, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        cliente_id = excluded.cliente_id,
        forma_pago = excluded.forma_pago,
        referencia_pago = excluded.referencia_pago,
        lineas_json = excluded.lineas_json,
        actualizado_en = excluded.actualizado_en
      ''',
      [
        clienteId,
        carrito.formaDePago.codigo,
        carrito.referenciaPago,
        jsonEncode([for (final l in carrito.lineas) _aMapa(l)]),
        ahora,
      ],
    );
  }

  /// Recupera el borrador, o `null` si no hay ninguno.
  ///
  /// Un borrador ilegible —esquema viejo, JSON truncado por un apagón a media
  /// escritura— se descarta en silencio y se empieza de cero. Es lo mismo que
  /// pasaría sin borrador, y mucho mejor que dejar la app sin abrir.
  BorradorDeCarrito? leer() {
    final filas = _db.select(
      'SELECT cliente_id, forma_pago, referencia_pago, lineas_json '
      '  FROM carrito_borrador WHERE id = 1',
    );
    if (filas.isEmpty) return null;

    try {
      final f = filas.single;
      final crudas = jsonDecode(f['lineas_json'] as String) as List;
      final lineas = [
        for (final c in crudas) _deMapa((c as Map).cast<String, Object?>()),
      ];
      if (lineas.isEmpty) return null;

      return BorradorDeCarrito(
        clienteId: f['cliente_id'] as String,
        carrito: Carrito(
          lineas: lineas,
          formaDePago: FormaDePago.deCodigo(f['forma_pago'] as String?),
          referenciaPago: f['referencia_pago'] as String?,
        ),
      );
    } on Object {
      limpiar();
      return null;
    }
  }

  void limpiar() => _db.execute('DELETE FROM carrito_borrador');

  static Map<String, Object?> _aMapa(LineaCarrito l) {
    final p = l.presentacion;
    return {
      'cantidad': l.cantidad.texto,
      'producto_id': p.productoId,
      'sku': p.sku,
      'nombre': p.nombre,
      'unidad_codigo': p.unidadCodigo,
      'unidad_base_codigo': p.unidadBaseCodigo,
      'factor': p.factor.texto,
      'precio': p.precio.texto,
      'lista_precios_id': p.listaPreciosId,
      'lista_precios_version': p.listaPreciosVersion,
      'tasa_iva': p.tasaIva,
      'es_default': p.esDefault,
      'codigo_barras': p.codigoBarras,
    };
  }

  static LineaCarrito _deMapa(Map<String, Object?> m) => LineaCarrito(
        cantidad: Cantidad.deTexto(m['cantidad']! as String),
        presentacion: PresentacionVendible(
          productoId: m['producto_id']! as String,
          sku: m['sku']! as String,
          nombre: m['nombre']! as String,
          unidadCodigo: m['unidad_codigo']! as String,
          unidadBaseCodigo: (m['unidad_base_codigo'] as String?) ?? 'PZA',
          factor: Factor.deTexto(m['factor']! as String),
          precio: Precio.deTexto(m['precio']! as String),
          listaPreciosId: m['lista_precios_id']! as String,
          listaPreciosVersion: m['lista_precios_version']! as int,
          tasaIva: (m['tasa_iva'] as num?)?.toDouble() ?? 0,
          esDefault: (m['es_default'] as bool?) ?? false,
          codigoBarras: m['codigo_barras'] as String?,
        ),
      );
}
