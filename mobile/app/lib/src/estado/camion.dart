/// Lo que el camión trae ahora mismo.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/repo_catalogo.dart';
import 'carrito.dart';

/// El texto por el que se está filtrando el inventario del camión.
final busquedaCamionProvider = StateProvider<String>((_) => '');

/// El inventario del camión, producto por producto.
///
/// Sale de `existencias_camion.cant_actual`, que es un **saldo**, no el conteo de
/// una carga: la carga lo sube, cada venta y cada merma lo bajan. Por eso esta
/// pantalla no tiene que calcular nada — y por eso el número que muestra es el que
/// el vendedor debería encontrar si abre la caja y cuenta.
final inventarioCamionProvider = Provider<List<ProductoDelCamion>>((ref) {
  final busqueda = ref.watch(busquedaCamionProvider);
  return ref.watch(repoCatalogoProvider).enElCamion(busqueda: busqueda);
});
