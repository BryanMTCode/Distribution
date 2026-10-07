/// El portal de la oficina en el teléfono: lo mismo que el dashboard, en el
/// bolsillo.
///
/// Pedido en operación (octubre 2026): «que el gerente pueda ver lo mismo que en
/// el dashboard: por días, por periodos y por vendedor». Una barra abajo, como
/// el menú del panel:
///
///   · **Día**: el tablero de un día, con calendario para elegir cuál.
///   · **Periodo**: semana, mes o fechas a mano, por vendedor y por día.
///   · **Empresa**: cuántos clientes, vendedores, artículos; la cartera.
///   · **Vendedores**: cada uno, su camión y todo lo que hizo.
///   · **Cargas**: subirle mercancía a un camión.
///
/// Cada pestaña aparece solo si el usuario tiene su permiso. La UI oculta; el
/// servidor prohíbe de todos modos.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/cargas.dart';
import '../../estado/vendedores.dart';
import 'cargas.dart';
import 'empresa.dart';
import 'panel.dart';
import 'periodo.dart';
import 'vendedores.dart';

class PortalDeOficina extends ConsumerStatefulWidget {
  const PortalDeOficina({super.key, required this.nombre});

  final String nombre;

  @override
  ConsumerState<PortalDeOficina> createState() => _EstadoPortal();
}

class _EstadoPortal extends ConsumerState<PortalDeOficina> {
  /// Las pestañas que ya se abrieron. Las demás no se construyen: cada una le
  /// pregunta al servidor al nacer, y abrir la app no debe hacer cuatro
  /// consultas para mostrar una.
  final Set<int> _abiertas = {};

  @override
  Widget build(BuildContext context) {
    final nombre = widget.nombre;
    final pestanas = <(Key, IconData, String, Widget)>[
      (
        const Key('nav_dia'),
        Icons.today_outlined,
        'Día',
        PantallaGerencia(nombre: nombre),
      ),
      (
        const Key('nav_periodo'),
        Icons.date_range_outlined,
        'Periodo',
        const PantallaPeriodo(),
      ),
      (
        const Key('nav_empresa'),
        Icons.business_outlined,
        'Empresa',
        const PantallaEmpresa(),
      ),
      if (ref.watch(puedeVerVendedoresProvider))
        (
          const Key('boton_vendedores'),
          Icons.groups_outlined,
          'Vendedores',
          const PantallaVendedores(),
        ),
      if (ref.watch(puedeCargarProvider))
        (
          const Key('boton_cargas'),
          Icons.local_shipping_outlined,
          'Cargas',
          const PantallaCargas(),
        ),
    ];
    final elegida = ref.watch(pestanaDeOficinaProvider).clamp(0, pestanas.length - 1);
    _abiertas.add(elegida);

    return Scaffold(
      key: const Key('portal_de_oficina'),
      // IndexedStack y no un cambio de cuerpo: cada pestaña conserva lo que se
      // estaba viendo —el periodo elegido, el vendedor abierto— al ir y volver.
      body: IndexedStack(
        index: elegida,
        children: [
          for (final (i, p) in pestanas.indexed)
            _abiertas.contains(i) ? p.$4 : const SizedBox.shrink(),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: elegida,
        onDestinationSelected: (i) => ref.read(pestanaDeOficinaProvider.notifier).state = i,
        destinations: [
          for (final p in pestanas)
            NavigationDestination(key: p.$1, icon: Icon(p.$2), label: p.$3),
        ],
      ),
    );
  }
}
