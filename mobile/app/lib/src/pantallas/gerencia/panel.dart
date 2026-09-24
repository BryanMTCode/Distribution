/// Panel de Gerencia (Fase 7).
///
/// Existe desde ahora porque el `PortalPorRol` tiene que resolver los dos
/// caminos: si el perfil de gerencia cayera en la pantalla del vendedor,
/// tendría a la vista operaciones que no le corresponden.
library;

import 'package:flutter/material.dart';

class PantallaGerencia extends StatelessWidget {
  const PantallaGerencia({super.key, required this.nombre});

  final String nombre;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Gerencia')),
        body: Center(
          key: const Key('panel_gerencia'),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.insights_outlined, size: 56),
                const SizedBox(height: 16),
                Text('Hola, $nombre'),
                const SizedBox(height: 8),
                const Text(
                  'El panel de monitoreo llega en la Fase 7.\n'
                  'Cada métrica vendrá con la antigüedad de sus datos: '
                  '"tiempo real" es "tiempo real de lo sincronizado".',
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      );
}
