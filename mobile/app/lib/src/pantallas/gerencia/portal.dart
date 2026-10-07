/// El portal de la oficina en el teléfono: lo mismo que el dashboard, en el
/// bolsillo.
///
/// Pedido en operación (octubre 2026): «que el gerente pueda ver lo mismo que en
/// el dashboard: por días, por periodos y por vendedor». Una barra abajo, como
/// el menú del panel:
///
///   · **Tablero**: un día (completo) o un periodo (por vendedor y por día).
///   · **Empresa**: cuántos clientes, vendedores, artículos; la cartera.
///   · **Vendedores**: cada uno, su camión y todo lo que hizo.
///   · **Clientes**: quién debe y desde cuándo, qué compra, su crédito.
///   · **Camiones**: cargar y hacer el corte del día.
///
/// Cada pestaña aparece solo si el usuario tiene su permiso. La UI oculta; el
/// servidor prohíbe de todos modos.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/cargas.dart';
import '../../estado/vendedores.dart';
import 'camiones.dart';
import 'clientes_oficina.dart';
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
      // Día y periodo en una sola pestaña: ver `PantallaGerencia`.
      (
        const Key('nav_tablero'),
        Icons.insights_outlined,
        'Tablero',
        PantallaGerencia(nombre: nombre),
      ),
      (
        const Key('nav_empresa'),
        Icons.business_outlined,
        'Empresa',
        const PantallaEmpresa(),
      ),
      if (ref.watch(puedeVerVendedoresProvider)) ...[
        (
          const Key('boton_vendedores'),
          Icons.groups_outlined,
          'Vendedores',
          const PantallaVendedores(),
        ),
        (
          const Key('nav_clientes'),
          Icons.storefront_outlined,
          'Clientes',
          const PantallaClientesDeOficina(),
        ),
      ],
      // Cargar y cortar: lo que se hace con el camión enfrente.
      if (ref.watch(puedeCargarProvider) || ref.watch(puedeCortarProvider))
        (
          const Key('nav_camiones'),
          Icons.local_shipping_outlined,
          'Camiones',
          const PantallaCamiones(),
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
