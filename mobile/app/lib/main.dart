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
}

String _generarLlave() {
  // 32 bytes de aleatoriedad criptográfica, en hexadecimal.
  final aleatorio = Random.secure();
  return List.generate(32, (_) => aleatorio.nextInt(256))
      .map((b) => b.toRadixString(16).padLeft(2, '0'))
      .join();
}
