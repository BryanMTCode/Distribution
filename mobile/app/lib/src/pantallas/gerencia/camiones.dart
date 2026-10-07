/// La pestaña Camiones: subirle mercancía (Cargas) y contarla al regresar
/// (Corte del día). Las dos son lo que se hace con el camión enfrente, en el
/// patio, y por eso van juntas.
///
/// Cada una aparece solo con su permiso (`inventario.cargar`,
/// `inventario.liquidar`); con una sola, se muestra directo, sin pestañas.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/cargas.dart';
import '../../estado/sesion.dart';
import 'cargas.dart';
import 'cortes.dart';

/// Si se muestra el corte. La UI oculta; el servidor prohíbe.
final puedeCortarProvider = Provider<bool>(
  (ref) => switch (ref.watch(sesionProvider)) {
    SesionDeGerencia(:final perfil) => perfil.puedeCortar,
    SesionAbierta(:final credencial) => credencial.puede('inventario.liquidar'),
    SinSesion() => false,
  },
);

class PantallaCamiones extends ConsumerWidget {
  const PantallaCamiones({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final cargar = ref.watch(puedeCargarProvider);
    final cortar = ref.watch(puedeCortarProvider);
    if (cargar && !cortar) return const PantallaCargas();
    if (cortar && !cargar) return const PantallaCortes();
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        key: const Key('pantalla_camiones'),
        appBar: AppBar(
          title: const Text('Camiones'),
          bottom: const TabBar(
            tabs: [
              Tab(key: Key('pestana_cargas'), text: 'Cargas'),
              Tab(key: Key('pestana_cortes'), text: 'Corte del día'),
            ],
          ),
        ),
        body: const TabBarView(
          children: [
            PantallaCargas(conBarra: false),
            PantallaCortes(conBarra: false),
          ],
        ),
      ),
    );
  }
}
