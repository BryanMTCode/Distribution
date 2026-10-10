/// Lo cancelado, aparte y plegado al final de la lista.
///
/// Pedido de la dirección (octubre 2026, ADR 0002 §95): «si borro una
/// transacción, que no siga apareciendo con todas las demás, sino que salga en
/// una parte que diga eliminadas». Lo usan las entradas, las cargas, los
/// traspasos, los movimientos del vendedor y las compras del cliente: lo que
/// cuenta arriba, lo cancelado abajo y cerrado, por si hay que consultarlo.
library;

import 'package:flutter/material.dart';

/// Los estados que quieren decir «ya no cuenta».
const estadosCancelados = {'cancelada', 'cancelado', 'rechazado', 'rechazada'};

bool estaCancelado(String? estado) => estadosCancelados.contains(estado);

class SeccionDeCanceladas extends StatelessWidget {
  const SeccionDeCanceladas({
    super.key,
    required this.renglones,
    this.titulo = 'Canceladas',
  });

  final List<Widget> renglones;
  final String titulo;

  @override
  Widget build(BuildContext context) {
    if (renglones.isEmpty) return const SizedBox.shrink();
    return Card(
      key: const Key('seccion_canceladas'),
      margin: const EdgeInsets.only(top: 12),
      child: ExpansionTile(
        key: const Key('abrir_canceladas'),
        leading: const Icon(Icons.block_outlined),
        title: Text('$titulo (${renglones.length})'),
        subtitle: const Text('No cuentan en las cifras'),
        children: renglones,
      ),
    );
  }
}
