/// Catálogo vendible, leído de la base local.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL PRECIO SE RESUELVE AQUÍ, Y EN UN SOLO LUGAR
/// ─────────────────────────────────────────────────────────────────────────
/// El cliente trae su `lista_precios_id`; con eso y la presentación se saca el
/// precio de la tabla `precios`. Si no hay renglón para esa combinación, la
/// presentación **no se ofrece**: no se construye una con precio cero, porque un
/// renglón de $0.00 en un ticket es peor que un producto que no aparece en la
/// pantalla.
///
/// Esa es también la forma de la regla de negocio (ADR 0002 §7): el precio no
/// viaja desde la interfaz hacia el dominio, sale del catálogo. La pantalla no
/// tiene de dónde tomar un precio distinto.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart';

/// Un producto del catálogo con las presentaciones que se le pueden vender a
/// **este** cliente.
class ProductoEnCatalogo {
  const ProductoEnCatalogo({
    required this.id,
    required this.sku,
    required this.nombre,
    required this.presentaciones,
    required this.existenciaBase,
    this.codigoBarras,
    this.vaEnLaCarga = true,
  });

  final String id;
  final String sku;
  final String nombre;
  final String? codigoBarras;

  /// Ordenadas: la presentación por omisión primero.
  final List<PresentacionVendible> presentaciones;

  /// Lo que queda arriba del camión, en unidad base.
  final Cantidad existenciaBase;

  /// `false` cuando el producto existe en el catálogo pero no subió al camión
  /// hoy. Se muestra agotado, no se esconde: el vendedor necesita saber que
  /// existe para pedirlo mañana.
  final bool vaEnLaCarga;

  PresentacionVendible get porOmision => presentaciones.first;

  bool get agotado => !vaEnLaCarga || existenciaBase.esCero;
}

class RepoCatalogo {
  const RepoCatalogo(this._db);

  final Database _db;

  /// El catálogo cotizado para la lista de precios de un cliente.
  ///
  /// Una sola consulta con todo junto: el teléfono es de gama baja y una lista
  /// de 400 productos que hiciera una consulta por producto se sentiría lenta
  /// en la mano, que es donde se nota.
  List<ProductoEnCatalogo> paraCliente({
    required String listaPreciosId,
    String? busqueda,
    int limite = 400,
  }) {
    final filtro = (busqueda ?? '').trim();
    final tieneFiltro = filtro.isNotEmpty;

    final filas = _db.select(
      '''
      SELECT p.id,
             p.sku,
             p.nombre,
             p.codigo_barras,
             p.tasa_iva,
             u.unidad_codigo,
             u.factor,
             u.es_default,
             pr.precio,
             pr.version,
             e.producto_id  AS en_carga,
             COALESCE(e.cant_actual, 0) AS existencia
        FROM productos p
        JOIN producto_unidades u ON u.producto_id = p.id
        JOIN precios pr ON pr.producto_id = p.id
                       AND pr.unidad_codigo = u.unidad_codigo
                       AND pr.lista_id = ?1
        LEFT JOIN existencias_camion e ON e.producto_id = p.id
       WHERE p.activo = 1
         AND (?2 = 0 OR p.nombre LIKE ?3 OR p.sku LIKE ?3 OR p.codigo_barras = ?4)
       ORDER BY p.nombre, u.es_default DESC, u.factor DESC
       LIMIT ?5
      ''',
      [listaPreciosId, tieneFiltro ? 1 : 0, '%$filtro%', filtro, limite * 4],
    );

    // Se agrupan por producto conservando el orden que dio SQL.
    final porProducto = <String, List<Row>>{};
    for (final f in filas) {
      porProducto.putIfAbsent(f['id'] as String, () => []).add(f);
    }

    final productos = <ProductoEnCatalogo>[];
    for (final entrada in porProducto.entries) {
      if (productos.length >= limite) break;
      final primera = entrada.value.first;
      productos.add(
        ProductoEnCatalogo(
          id: entrada.key,
          sku: primera['sku'] as String,
          nombre: primera['nombre'] as String,
          codigoBarras: primera['codigo_barras'] as String?,
          vaEnLaCarga: primera['en_carga'] != null,
          existenciaBase: Cantidad.deBase((primera['existencia'] as num).toDouble()),
          presentaciones: [
            for (final f in entrada.value)
              PresentacionVendible(
                productoId: entrada.key,
                sku: f['sku'] as String,
                nombre: f['nombre'] as String,
                unidadCodigo: f['unidad_codigo'] as String,
                factor: Factor.deBase((f['factor'] as num).toDouble()),
                // El precio cruza de REAL a entero exacto aquí, una sola vez.
                precio: Precio.deBase((f['precio'] as num).toDouble()),
                listaPreciosId: listaPreciosId,
                listaPreciosVersion: f['version'] as int,
                tasaIva: (f['tasa_iva'] as num).toDouble(),
                esDefault: (f['es_default'] as int) == 1,
                codigoBarras: f['codigo_barras'] as String?,
              ),
          ],
        ),
      );
    }
    return productos;
  }

  /// Lo que trae el camión hoy, en unidad base por producto.
  ///
  /// Se lee entero y de golpe: el carrito lo consulta en cada toque de "+", y
  /// una consulta por toque se sentiría pegajosa en un teléfono de gama baja.
  ExistenciasCamion existencias() {
    final filas = _db.select(
      'SELECT producto_id, cant_actual FROM existencias_camion',
    );
    return ExistenciasCamion({
      for (final f in filas)
        f['producto_id'] as String:
            Cantidad.deBase((f['cant_actual'] as num).toDouble()),
    });
  }

  /// La lista de precios del cliente, o `null` si no tiene una asignada.
  ///
  /// Sin lista no se puede cotizar nada, y eso se le dice al vendedor en vez de
  /// mostrarle un catálogo vacío que parece un error de la app.
  String? listaDeCliente(String clienteId) {
    final filas = _db.select(
      'SELECT lista_precios_id FROM clientes WHERE id = ?',
      [clienteId],
    );
    if (filas.isEmpty) return null;
    return filas.single['lista_precios_id'] as String?;
  }

  /// `true` cuando el camión no trae carga: ningún producto, ninguna existencia.
  bool get sinCargaActiva =>
      (_db.select('SELECT COUNT(*) AS n FROM existencias_camion').single['n']
          as int) ==
      0;
}
