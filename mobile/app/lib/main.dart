/// Arranque de la app del vendedor.
library;

import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'src/app.dart';
import 'src/datos/almacen_seguro.dart';
import 'src/datos/base_local.dart';
import 'src/estado/sesion.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // TODO EL ARRANQUE VA EN UN try, Y ESO ES LA LECCIÓN DE UN FALLO EN CAMPO.
  //
  // Aquí se lee el Keystore y se abre la base cifrada ANTES de `runApp`. Si algo
  // de eso lanza, no llega a existir un árbol de widgets: el vendedor ve la
  // PANTALLA NEGRA, sin un mensaje, sin un botón, sin nada que reportar por
  // teléfono más que «no abre».
  //
  // Pasó: el esquema local se ejecutaba en cada arranque sin `IF NOT EXISTS`, así
  // que la app abría bien la primera vez y nunca más —moría con «table
  // sync_estado already exists»—. Eso ya está arreglado en el esquema, pero la
  // fragilidad era esta función: cualquier otro fallo de arranque habría dado el
  // mismo negro.
  //
  // Con esto, un arranque roto muestra qué pasó y qué hacer. No arregla la causa;
  // la hace reportable, que es la diferencia entre media hora y una mañana.
  try {
    const almacen = AlmacenSeguroDelSistema();

    // La llave de SQLCipher se genera una vez y vive en el Keystore, nunca en la
    // base que protege.
    var llave = await almacen.leer('llave_base_local');
    if (llave == null) {
      llave = _generarLlave();
      await almacen.escribir('llave_base_local', llave);
    }

    final base = await BaseLocal.abrir(llave: llave);

    runApp(
      ProviderScope(
        overrides: [
          almacenSeguroProvider.overrideWithValue(almacen),
          baseLocalProvider.overrideWithValue(base),
        ],
        child: const AppDsd(),
      ),
    );
  } on Object catch (e, traza) {
    runApp(PantallaDeArranqueRoto(error: e, traza: traza));
  }
}

/// Lo que se muestra cuando la app no pudo ni arrancar.
///
/// Deliberadamente no depende de NADA: ni de la base, ni del almacén seguro, ni
/// de un provider. Si dependiera de alguno, el fallo que intenta explicar podría
/// tumbarla también y volveríamos al negro.
class PantallaDeArranqueRoto extends StatelessWidget {
  const PantallaDeArranqueRoto({required this.error, required this.traza, super.key});

  final Object error;
  final StackTrace traza;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      home: Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.error_outline, size: 48),
                const SizedBox(height: 16),
                const Text(
                  'La app no pudo arrancar',
                  style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 12),
                const Text(
                  'No es falta de señal: es un problema del propio equipo. '
                  'Avisa a la oficina y enséñale esta pantalla.\n\n'
                  'TUS VENTAS NO SE PERDIERON: lo que no se había subido sigue '
                  'guardado en el teléfono. No desinstales la app — desinstalar '
                  'SÍ las borra.',
                ),
                const SizedBox(height: 20),
                const Text('Detalle para la oficina:',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Expanded(
                  child: SingleChildScrollView(
                    child: SelectableText(
                      '$error\n\n$traza',
                      style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

String _generarLlave() {
  // 32 bytes de aleatoriedad criptográfica, en hexadecimal.
  final aleatorio = Random.secure();
  return List.generate(32, (_) => aleatorio.nextInt(256))
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join();
}
