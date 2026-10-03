/// Lo que se muestra cuando el APK se compiló sin servidor.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA PANTALLA EXISTE PARA QUE EL ERROR NO SE DISFRACE DE OTRO
/// ─────────────────────────────────────────────────────────────────────────
/// Sin ella, un APK de release construido sin `--dart-define=DSD_BASE_URL`
/// apunta a `api.localhost`, se instala, abre, y falla al entrar con un error
/// de red. El vendedor reporta «no hay internet» —porque es lo que la pantalla
/// de login le diría— y la mañana se va en revisar el túnel, el router y la
/// señal del teléfono. La falla no está en ninguno de los tres: está en cómo se
/// compiló el binario, y el binario es el único que lo sabe.
///
/// Así que lo dice, antes del login, sin botones. No hay nada que el vendedor
/// pueda hacer desde el teléfono —el servidor se fija al compilar, no en una
/// pantalla de ajustes, y eso es a propósito (ver `sincronizacion.dart`)—, y un
/// botón que no sirve haría concluir que la app está rota en vez de que el APK
/// está mal armado. Lo que sí lleva es el comando que lo arregla, para quien
/// recibe la llamada.
library;

import 'package:flutter/material.dart';

class PantallaSinServidor extends StatelessWidget {
  const PantallaSinServidor({super.key});

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;

    return Scaffold(
      key: const Key('apk_sin_servidor'),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.build_circle_outlined, size: 64, color: esquema.error),
                const SizedBox(height: 20),
                Text(
                  'Esta app se instaló sin servidor',
                  style: Theme.of(context).textTheme.headlineSmall,
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 14),
                const Text(
                  'No es falla del teléfono ni de la señal: este APK se compiló '
                  'sin decirle a qué servidor conectarse, así que no hay con quién '
                  'sincronizar.',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 24),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: esquema.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Para quien lo compiló',
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                      const SizedBox(height: 8),
                      const SelectableText(
                        'make apk DSD_BASE_URL=https://api.tudominio.com',
                        style: TextStyle(fontFamily: 'monospace', fontSize: 13),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'Ese comando no deja construir el APK sin la dirección. '
                        'Este se armó por otro camino.',
                        style: TextStyle(fontSize: 12),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                Text(
                  'Repórtalo a la oficina y NO DESINSTALES la app. Si este APK '
                  'se instaló encima de uno que ya funcionaba, las ventas y los '
                  'cobros que no se hayan subido siguen guardados aquí, y '
                  'desinstalar se los lleva.',
                  style: TextStyle(fontSize: 12, color: esquema.onSurfaceVariant),
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
