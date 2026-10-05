/// Lo que el camión trae ahora mismo.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/repo_catalogo.dart';
import 'carrito.dart';
import 'sesion.dart';

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

/// Un ajuste que la oficina le hizo a este camión, ya aplicado.
class AjusteDeOficina {
  const AjusteDeOficina({required this.folio, required this.nota});

  final String? folio;
  final String? nota;
}

/// Los últimos ajustes que la oficina le hizo al camión.
///
/// ───────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTO SE MUESTRA
/// ───────────────────────────────────────────────────────────────────────────
/// Gerencia puede corregir el inventario del camión desde el panel, y el ajuste
/// llega en la siguiente sincronización. Si solo cambiara el número, el vendedor
/// abriría «Mi camión», vería 12 donde ayer había 30, y no tendría forma de saber si
/// se lo ajustaron o si la app perdió una carga.
///
/// La nota la escribió gerencia al guardar el ajuste, y es obligatoria justamente
/// para que aquí haya algo que leer.
final ajustesDeOficinaProvider = Provider<List<AjusteDeOficina>>((ref) {
  final db = ref.watch(baseLocalProvider).db;
  return db
      .select(
        'SELECT folio, nota FROM ajustes_camion_aplicados '
        ' ORDER BY aplicado_en DESC LIMIT 5',
      )
      .map(
        (f) => AjusteDeOficina(
          folio: f['folio'] as String?,
          nota: f['nota'] as String?,
        ),
      )
      .toList();
});
