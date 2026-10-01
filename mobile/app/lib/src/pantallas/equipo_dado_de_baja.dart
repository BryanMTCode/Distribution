/// Lo que se le muestra al equipo que la oficina dio de baja (Fase 9).
///
/// ─────────────────────────────────────────────────────────────────────────
/// DOS PANTALLAS PORQUE SON DOS SITUACIONES OPUESTAS
/// ─────────────────────────────────────────────────────────────────────────
/// **Bloqueado** — hay orden de borrado y el teléfono todavía trae operaciones
/// sin entregar. NO se borró nada. Lo único que hay que hacer es buscar señal,
/// y la pantalla lo dice con un número: «quedan 7 operaciones por subir». Ese
/// número es la razón de que el equipo no se haya borrado todavía, y verlo
/// convierte «mi teléfono se bloqueó» en «tengo que conectarme».
///
/// **Borrado** — ya ocurrió. No hay base, no hay credencial, no hay nada que
/// mostrar ni nada que intentar. La pantalla no ofrece botones porque no hay
/// ninguna salida que dar desde el teléfono, y un botón que no sirve es peor
/// que ninguno: se pica, no pasa nada, y la persona concluye que la app está
/// rota en vez de que el equipo está de baja.
///
/// En las dos se muestra el motivo que escribió la oficina. Es lo que convierte
/// «mi teléfono dejó de funcionar» en «me dieron de baja y ya sé por qué», y es
/// la diferencia entre una llamada de reclamo y ninguna.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../estado/sincronizacion.dart';

class PantallaEquipoBloqueado extends ConsumerWidget {
  const PantallaEquipoBloqueado({
    super.key,
    required this.pendientes,
    this.motivo,
  });

  final int pendientes;
  final String? motivo;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final esquema = Theme.of(context).colorScheme;
    final sincronizando = ref.watch(syncProvider) is SyncEnCurso;

    return Scaffold(
      key: const Key('equipo_bloqueado'),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.phonelink_lock_outlined, size: 64, color: esquema.error),
                const SizedBox(height: 20),
                Text(
                  'Este equipo fue dado de baja',
                  style: Theme.of(context).textTheme.headlineSmall,
                  textAlign: TextAlign.center,
                ),
                if (motivo != null) ...[
                  const SizedBox(height: 10),
                  Text(motivo!, textAlign: TextAlign.center),
                ],
                const SizedBox(height: 24),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: esquema.errorContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(
                    children: [
                      Text(
                        '$pendientes',
                        style: TextStyle(
                          fontSize: 44,
                          fontWeight: FontWeight.bold,
                          color: esquema.onErrorContainer,
                        ),
                      ),
                      Text(
                        pendientes == 1
                            ? 'operación todavía sin subir'
                            : 'operaciones todavía sin subir',
                        style: TextStyle(color: esquema.onErrorContainer),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        // El número es la razón de que el equipo no se haya
                        // borrado. Decirlo convierte «se bloqueó» en «tengo que
                        // conectarme».
                        'Nada se ha borrado. Conéctate a internet para que '
                        'suban; el equipo se limpiará solo cuando no quede '
                        'ninguna.',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: esquema.onErrorContainer),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    key: const Key('boton_entregar_pendientes'),
                    onPressed: sincronizando
                        ? null
                        : () => ref.read(syncProvider.notifier).sincronizar(),
                    icon: sincronizando
                        ? const SizedBox(
                            height: 18,
                            width: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.cloud_upload_outlined),
                    label: const Text('Buscar señal y entregar'),
                  ),
                ),
                const SizedBox(height: 16),
                Text(
                  'Si no logras conectarte, entrega el teléfono en la oficina '
                  'SIN borrar la app: lo que trae dentro todavía se puede '
                  'recuperar.',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class PantallaEquipoBorrado extends StatelessWidget {
  const PantallaEquipoBorrado({super.key, this.motivo});

  final String? motivo;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: const Key('equipo_borrado'),
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.verified_outlined, size: 64),
                const SizedBox(height: 20),
                Text(
                  'Este equipo quedó limpio',
                  style: Theme.of(context).textTheme.headlineSmall,
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 12),
                Text(
                  'La oficina dio de baja este teléfono. Todo lo que tenía '
                  'pendiente se subió antes de borrarse: no se perdió ninguna '
                  'operación.',
                  textAlign: TextAlign.center,
                ),
                if (motivo != null) ...[
                  const SizedBox(height: 16),
                  Text(
                    motivo!,
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
                const SizedBox(height: 24),
                // Sin botones, a propósito: no hay ninguna salida que dar desde
                // el teléfono, y un botón que no sirve hace concluir que la app
                // está rota en vez de que el equipo está de baja.
                Text(
                  'Si necesitas volver a usarlo, la oficina tiene que '
                  'registrarlo de nuevo.',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
