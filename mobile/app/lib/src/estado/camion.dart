/// Lo que el camión trae ahora mismo.
library;

import 'package:dsd_core/dsd_core.dart';
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
  ref.watch(revisionDelCamionProvider);
  final busqueda = ref.watch(busquedaCamionProvider);
  return ref.watch(repoCatalogoProvider).enElCamion(busqueda: busqueda);
});

/// Un ajuste que la oficina le hizo a este camión, ya aplicado.
class AjusteDeOficina {
  const AjusteDeOficina({
    required this.folio,
    required this.nota,
    this.producto,
    this.unidad,
    this.delta,
  });

  final String? folio;
  final String? nota;

  /// El artículo que cambió. Nulo si el catálogo del teléfono todavía no lo
  /// conoce.
  final String? producto;
  final String? unidad;

  /// Cuánto subió (positivo) o bajó (negativo), en unidad base. Nulo en los
  /// ajustes que llegaron antes de que el teléfono lo guardara (ADR 0002 §145).
  final Cantidad? delta;

  /// «+24 PZA» o «-6 PZA»: lo que el vendedor compara contra la caja.
  String? get cambio {
    final d = delta;
    if (d == null) return null;
    final signo = d.milesimos > 0 ? '+' : '';
    return '$signo${d.textoCorto}${unidad == null ? '' : ' $unidad'}';
  }
}

/// Los ajustes que la oficina le hizo hoy al camión, el más nuevo primero.
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
///
/// **Solo los del día** (ADR 0002 §145): «que solo se le muestre ese día al
/// vendedor, no que quede el mensaje de manera permanente». Mañana el número ya
/// es el de siempre y el aviso de ayer solo estorba. El registro se queda —es lo
/// que impide aplicar dos veces el mismo ajuste—; lo que se filtra es el aviso,
/// con el mismo día operativo que estampa las ventas. `autoDispose`: al volver a
/// abrir «Mi camión» otro día se vuelve a calcular.
///
/// Y **qué cambió**: «y que le muestre qué fue lo que cambió». Cada ajuste trae
/// su artículo y su diferencia; con solo el folio y la nota el vendedor sabía que
/// algo cambió, pero tenía que buscar qué renglón.
final ajustesDeOficinaProvider = Provider.autoDispose<List<AjusteDeOficina>>((ref) {
  ref.watch(revisionDelCamionProvider);
  final hoy = diaOperativoDe(ref.watch(relojProvider)());
  final db = ref.watch(baseLocalProvider).db;
  return db
      .select(
        'SELECT a.folio, a.nota, a.aplicado_en, a.delta, '
        '       p.nombre AS producto, p.unidad_base '
        '  FROM ajustes_camion_aplicados a '
        '  LEFT JOIN productos p ON p.id = a.producto_id '
        // Un conteo de arranque toca cientos de artículos en un día.
        ' ORDER BY a.aplicado_en DESC, a.folio DESC LIMIT 1000',
      )
      .where((f) {
        final cuando = DateTime.tryParse(f['aplicado_en'] as String? ?? '');
        return cuando != null && diaOperativoDe(cuando) == hoy;
      })
      .map(
        (f) => AjusteDeOficina(
          folio: f['folio'] as String?,
          nota: f['nota'] as String?,
          producto: f['producto'] as String?,
          unidad: f['unidad_base'] as String?,
          delta: f['delta'] == null ? null : Cantidad.deBase(f['delta'] as num),
        ),
      )
      .toList();
});
