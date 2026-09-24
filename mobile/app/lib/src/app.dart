/// Portal por rol.
///
/// La app cambia por completo según quién entra. No son dos pestañas del mismo
/// árbol: son dos shells distintos, y el de gerencia **no compila contra la
/// maquinaria offline**. Son dos apps que comparten binario.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'estado/sesion.dart';
import 'pantallas/gerencia/panel.dart';
import 'pantallas/login.dart';
import 'pantallas/vendedor/clientes.dart';

class AppDsd extends StatelessWidget {
  const AppDsd({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'DSD Ruta',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorSchemeSeed: const Color(0xFF1B5E20),
          useMaterial3: true,
          // La app se usa a pleno sol en la calle: texto grande y contraste alto.
          visualDensity: VisualDensity.comfortable,
        ),
        home: const PortalPorRol(),
      );
}

class PortalPorRol extends ConsumerWidget {
  const PortalPorRol({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sesion = ref.watch(sesionProvider);

    return switch (sesion) {
      SinSesion() => const PantallaLogin(),
      SesionAbierta(:final credencial) => _porRol(credencial),
    };
  }

  Widget _porRol(CredencialLocal credencial) => switch (credencial.rol) {
        'vendedor' || 'supervisor' => const PantallaClientes(),
        _ => PantallaGerencia(nombre: credencial.nombre),
      };
}
