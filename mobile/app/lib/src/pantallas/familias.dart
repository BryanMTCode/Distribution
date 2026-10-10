/// Los artículos agrupados por familia, cada familia plegable.
///
/// Pedido de la dirección (octubre 2026, ADR 0002 §92): «agrupa también en la
/// app los artículos», y «que las agrupaciones se puedan desplegar o no». Lo usan
/// el catálogo y el camión del vendedor, la merma y la devolución, y las
/// existencias del gerente: todos igual, para que plegar se aprenda una vez.
///
/// La lista llega ya ordenada por familia —la consulta pone primero el orden de
/// la oficina y «Sin familia» al final—; aquí solo se parte en grupos, en el orden
/// en que llegan. Si nada tiene familia no se pinta ningún encabezado: un solo
/// grupo llamado «Sin familia» sería ruido.
library;

import 'package:flutter/material.dart';

const sinFamilia = 'Sin familia';

class GrupoDeFamilia<T> {
  GrupoDeFamilia(this.familia, this.elementos);

  final String familia;
  final List<T> elementos;
}

/// Los grupos, en el orden en que llegan sus primeros elementos.
List<GrupoDeFamilia<T>> porFamilia<T>(
  Iterable<T> elementos,
  String? Function(T) familiaDe,
) {
  final grupos = <String, GrupoDeFamilia<T>>{};
  for (final e in elementos) {
    final familia = familiaDe(e) ?? sinFamilia;
    grupos
        .putIfAbsent(familia, () => GrupoDeFamilia(familia, []))
        .elementos
        .add(e);
  }
  return grupos.values.toList();
}

/// Un renglón de la lista: el encabezado de una familia o uno de sus elementos.
sealed class EntradaPorFamilia<T> {
  const EntradaPorFamilia();
}

class EncabezadoEntrada<T> extends EntradaPorFamilia<T> {
  const EncabezadoEntrada(this.grupo, {required this.abierta});

  final GrupoDeFamilia<T> grupo;
  final bool abierta;
}

class ElementoEntrada<T> extends EntradaPorFamilia<T> {
  const ElementoEntrada(this.elemento);

  final T elemento;
}

/// La lista plana que pinta un `ListView.builder`: cada encabezado seguido de
/// sus elementos si la familia está desplegada.
///
/// Con [todoAbierto] —mientras se busca— se despliega todo: lo que se buscó no
/// debe quedar escondido en una familia que alguien plegó hace rato.
List<EntradaPorFamilia<T>> entradasPorFamilia<T>(
  List<T> elementos,
  String? Function(T) familiaDe, {
  required Set<String> plegadas,
  bool todoAbierto = false,
}) {
  final grupos = porFamilia(elementos, familiaDe);
  if (grupos.length == 1 && grupos.single.familia == sinFamilia) {
    return [for (final e in elementos) ElementoEntrada(e)];
  }
  return [
    for (final g in grupos) ...[
      EncabezadoEntrada(
        g,
        abierta: todoAbierto || !plegadas.contains(g.familia),
      ),
      if (todoAbierto || !plegadas.contains(g.familia))
        for (final e in g.elementos) ElementoEntrada(e),
    ],
  ];
}

/// ¿Vale la pena ofrecer «plegar todas»? Solo con dos familias o más.
bool hayFamilias<T>(List<T> elementos, String? Function(T) familiaDe) {
  final grupos = porFamilia(elementos, familiaDe);
  return grupos.length > 1 ||
      (grupos.length == 1 && grupos.single.familia != sinFamilia);
}

/// El encabezado de una familia: se toca para plegarla o desplegarla.
class EncabezadoDeFamilia extends StatelessWidget {
  const EncabezadoDeFamilia({
    super.key,
    required this.familia,
    required this.cuantos,
    required this.abierta,
    required this.alTocar,
    this.resumen,
  });

  final String familia;
  final int cuantos;
  final bool abierta;
  final VoidCallback alTocar;

  /// Lo que se dice a la derecha: cuánto vale, cuántas piezas.
  final String? resumen;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    return Material(
      key: Key('familia_$familia'),
      color: colores.surfaceContainerHighest,
      child: InkWell(
        onTap: alTocar,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 10, 12, 10),
          child: Row(
            children: [
              Icon(
                abierta ? Icons.expand_more : Icons.chevron_right,
                key: Key(
                  'pliegue_${familia}_${abierta ? 'abierta' : 'plegada'}',
                ),
              ),
              const SizedBox(width: 4),
              Expanded(
                child: Text(
                  '$familia · $cuantos',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              if (resumen != null)
                Text(
                  resumen!,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Plegar o desplegar todas de un toque, para la barra de arriba.
class BotonPlegarFamilias extends StatelessWidget {
  const BotonPlegarFamilias({
    super.key,
    required this.algunaPlegada,
    required this.alTocar,
  });

  final bool algunaPlegada;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) => IconButton(
    key: const Key('plegar_familias'),
    tooltip: algunaPlegada
        ? 'Desplegar todas las familias'
        : 'Plegar todas las familias',
    icon: Icon(algunaPlegada ? Icons.unfold_more : Icons.unfold_less),
    onPressed: alTocar,
  );
}

/// El estado de qué familias están plegadas, para el `State` de cada pantalla.
mixin FamiliasPlegables<W extends StatefulWidget> on State<W> {
  final Set<String> plegadas = {};

  void alternarFamilia(String familia) => setState(() {
    if (!plegadas.remove(familia)) plegadas.add(familia);
  });

  /// Si hay alguna plegada, se despliegan todas; si no, se pliegan todas.
  void alternarTodas<T>(List<T> elementos, String? Function(T) familiaDe) =>
      setState(() {
        if (plegadas.isNotEmpty) {
          plegadas.clear();
        } else {
          plegadas.addAll(
            porFamilia(elementos, familiaDe).map((g) => g.familia),
          );
        }
      });
}
