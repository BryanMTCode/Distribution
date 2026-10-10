/// «Mi camión»: lo que traigo ahora mismo.
///
/// ───────────────────────────────────────────────────────────────────────────
/// ES UN SALDO, NO EL CONTEO DE UNA CARGA
/// ───────────────────────────────────────────────────────────────────────────
/// El número que se muestra es `existencias_camion.cant_actual`: lo sube cada
/// carga confirmada, lo baja cada venta y cada merma. Esta pantalla no calcula
/// nada — y eso es deliberado. Si recalculara «cargado menos vendido» por su
/// cuenta, habría dos cuentas del mismo inventario y el día que discreparan nadie
/// sabría cuál creer.
///
/// Lee de SQLite, así que funciona sin señal: es lo que el vendedor consulta
/// delante del cliente para saber si puede surtirle.
///
/// Los renglones en CERO y en NEGATIVO se muestran. Un cero no es «no traigo
/// nada»: es «el sistema cree que no queda», y si el vendedor ve una caja en el
/// piso, ese renglón es justo el que hay que corregir. Un negativo significa que
/// se vendió más de lo que el sistema creía, y la liquidación lo va a cobrar.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_catalogo.dart';
import '../../estado/camion.dart';
import '../familias.dart';
import 'devolver_a_bodega.dart';

class PantallaCamion extends ConsumerStatefulWidget {
  const PantallaCamion({super.key});

  @override
  ConsumerState<PantallaCamion> createState() => _EstadoCamion();
}

String? _familiaDe(ProductoDelCamion p) => p.familia;

class _EstadoCamion extends ConsumerState<PantallaCamion>
    with FamiliasPlegables<PantallaCamion> {
  @override
  Widget build(BuildContext context) {
    final productos = ref.watch(inventarioCamionProvider);
    final busqueda = ref.watch(busquedaCamionProvider);
    final ajustes = ref.watch(ajustesDeOficinaProvider);
    final colores = Theme.of(context).colorScheme;

    final conExistencia = productos.where((p) => p.existenciaBase.milesimos > 0).length;
    final enNegativo = productos.where((p) => p.existenciaBase.milesimos < 0).length;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mi camión'),
        actions: [
          // Plegar o desplegar todas las familias (ADR 0002 §92).
          if (hayFamilias(productos, _familiaDe))
            BotonPlegarFamilias(
              algunaPlegada: plegadas.isNotEmpty,
              alTocar: () => alternarTodas(productos, _familiaDe),
            ),
          // La devolución a la bodega se entra DESDE AQUÍ y no desde el menú de la
          // lista de clientes: la pregunta «¿qué bajo?» se contesta mirando lo que
          // trae, y es la pantalla que el vendedor ya tiene abierta al final de la
          // ruta. Un menú más arriba la habría escondido.
          IconButton(
            key: const Key('boton_devolver_a_bodega'),
            tooltip: 'Devolver a la bodega',
            icon: const Icon(Icons.warehouse_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const PantallaDevolverABodega(),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: TextField(
              key: const Key('buscar_en_camion'),
              onChanged: (v) =>
                  ref.read(busquedaCamionProvider.notifier).state = v,
              decoration: const InputDecoration(
                hintText: 'Buscar producto o código',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),

          // El aviso de los ajustes de la oficina va ARRIBA del resumen, porque la
          // pregunta que trae al vendedor a esta pantalla es «¿por qué dice 12?».
          if (ajustes.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Container(
                key: const Key('aviso_ajustes_oficina'),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: colores.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.inventory_2_outlined,
                            size: 18, color: colores.outline),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            ajustes.length == 1
                                ? 'La oficina ajustó tu camión'
                                : 'La oficina hizo ${ajustes.length} ajustes a tu camión',
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ),
                      ],
                    ),
                    for (final a in ajustes)
                      if (a.nota != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(
                            '${a.folio ?? ''} «${a.nota}»',
                            style: TextStyle(fontSize: 12, color: colores.outline),
                          ),
                        ),
                  ],
                ),
              ),
            ),

          // El resumen va arriba porque es lo que se mira de reojo: cuántos
          // productos distintos traigo y si algo está en negativo.
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    conExistencia == 1
                        ? '1 producto con existencia'
                        : '$conExistencia productos con existencia',
                    key: const Key('resumen_camion'),
                    style: TextStyle(color: colores.outline, fontSize: 13),
                  ),
                ),
                if (enNegativo > 0)
                  Text(
                    '$enNegativo en negativo',
                    key: const Key('resumen_negativos'),
                    style: TextStyle(
                      color: colores.error,
                      fontSize: 13,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
              ],
            ),
          ),
          const Divider(height: 16),

          Expanded(
            child: productos.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Text(
                        busqueda.trim().isEmpty
                            // Sin carga el camión está vacío de verdad, y decirlo
                            // así evita que el vendedor crea que la app falla.
                            ? 'El camión está vacío. Cuando la oficina confirme '
                                'una carga y sincronices, aparecerá aquí.'
                            : 'Ningún producto coincide con «$busqueda».',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  )
                : _ListaPorFamilia(
                    entradas: entradasPorFamilia(
                      productos,
                      _familiaDe,
                      plegadas: plegadas,
                      todoAbierto: busqueda.trim().isNotEmpty,
                    ),
                    alTocarFamilia: alternarFamilia,
                  ),
          ),
        ],
      ),
    );
  }
}

/// Lo que trae, por familia: cada encabezado pliega o despliega la suya.
class _ListaPorFamilia extends StatelessWidget {
  const _ListaPorFamilia({required this.entradas, required this.alTocarFamilia});

  final List<EntradaPorFamilia<ProductoDelCamion>> entradas;
  final void Function(String) alTocarFamilia;

  @override
  Widget build(BuildContext context) => ListView.separated(
        key: const Key('lista_camion_vendedor'),
        itemCount: entradas.length,
        separatorBuilder: (_, __) => const Divider(height: 1),
        itemBuilder: (_, i) => switch (entradas[i]) {
          EncabezadoEntrada(:final grupo, :final abierta) => EncabezadoDeFamilia(
              familia: grupo.familia,
              cuantos: grupo.elementos.length,
              abierta: abierta,
              alTocar: () => alTocarFamilia(grupo.familia),
            ),
          ElementoEntrada(:final elemento) => _Renglon(producto: elemento),
        },
      );
}

class _Renglon extends StatelessWidget {
  const _Renglon({required this.producto});

  final ProductoDelCamion producto;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final negativo = producto.existenciaBase.milesimos < 0;
    final agotado = producto.existenciaBase.milesimos == 0;

    return ListTile(
      key: Key('camion_${producto.sku}'),
      title: Text(producto.nombre),
      subtitle: Text(
        // La equivalencia en cajas va en el subtítulo y no en el número grande:
        // el vendedor cuenta en cajas para cargar y en piezas para vender, y la
        // unidad base es la que no se presta a confusión.
        [producto.sku, _enPresentaciones(producto)]
            .where((t) => t.isNotEmpty)
            .join(' · '),
        style: TextStyle(fontSize: 12, color: colores.outline),
      ),
      trailing: Text(
        producto.existenciaBase.texto,
        style: TextStyle(
          fontSize: 17,
          fontWeight: FontWeight.bold,
          color: negativo
              ? colores.error
              : agotado
                  ? colores.outline
                  : null,
        ),
      ),
    );
  }

  /// «2 CAJA y 5.000 PZA» cuando hay una presentación mayor que la base.
  ///
  /// TODO EN ENTEROS, y no es purismo: `Cantidad` cuenta en milésimas y `Factor`
  /// en diezmilésimas, así que dividir los dos como doubles para «saber cuántas
  /// cajas» es exactamente la forma de que 240 piezas de una caja de 24 se vean
  /// como 9.9999 cajas. El vendedor lee ese renglón para decidir si puede surtir
  /// un pedido.
  String _enPresentaciones(ProductoDelCamion p) {
    final base = p.existenciaBase.milesimos;
    if (base <= 0) return '';

    final mayores = p.unidades
        .where((u) => u.factor.diezmilesimos > Factor.uno.diezmilesimos)
        .toList()
      ..sort((a, b) => b.factor.diezmilesimos.compareTo(a.factor.diezmilesimos));
    if (mayores.isEmpty) return '';

    // Las unidades, despacio, porque equivocarlas es lo que acaba en «7.9999
    // cajas»: `base` son MILÉSIMAS de unidad base y `porCaja` son DIEZMILÉSIMAS
    // de unidad base por caja. Así que
    //
    //   cajas = (base/1000) ÷ (porCaja/10000) = base × 10 ÷ porCaja
    //
    // y el factor en milésimas —para restar lo que ocupan esas cajas— es
    // `porCaja ÷ 10`. Mi primera versión multiplicaba por 10000 y daba 8000
    // cajas de 192 piezas; lo encontró la prueba.
    final porCaja = mayores.first.factor.diezmilesimos;
    final cajas = (base * 10) ~/ porCaja;
    if (cajas == 0) return '';
    final sueltas = base - cajas * (porCaja ~/ 10);

    final partes = ['$cajas ${mayores.first.codigo}'];
    if (sueltas > 0) partes.add('${_enMilesimas(sueltas)} ${p.unidadBase}');
    return partes.join(' y ');
  }

  /// Milésimas enteras a texto de tres decimales, sin pasar por un double.
  String _enMilesimas(int milesimos) {
    final enteros = milesimos ~/ 1000;
    final resto = (milesimos % 1000).toString().padLeft(3, '0');
    return '$enteros.$resto';
  }
}
