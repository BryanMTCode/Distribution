/// La impresora elegida en este teléfono (ADR 0002 §139).
///
/// Se guarda en `sync_estado`, en la base del teléfono: es de ESTE equipo, no
/// del vendedor —si otro vendedor usa el teléfono, la impresora del camión es la
/// misma— y un borrado a distancia la olvida con todo lo demás.
library;

import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/impresora_bluetooth.dart';
import 'sesion.dart';

const _clave = 'impresora_elegida';

final impresoraElegidaProvider =
    NotifierProvider<ControladorImpresora, ImpresoraEmparejada?>(
  ControladorImpresora.new,
);

class ControladorImpresora extends Notifier<ImpresoraEmparejada?> {
  @override
  ImpresoraEmparejada? build() {
    final fila = ref
        .watch(baseLocalProvider)
        .db
        .select('SELECT valor FROM sync_estado WHERE clave = ?', [_clave]);
    final valor = fila.isEmpty ? null : fila.single['valor'] as String?;
    if (valor == null) return null;
    try {
      return ImpresoraEmparejada.deMapa(jsonDecode(valor) as Map<Object?, Object?>);
    } on Object {
      // Ilegible es como no tenerla: se vuelve a elegir.
      return null;
    }
  }

  void elegir(ImpresoraEmparejada impresora) {
    ref.read(baseLocalProvider).db.execute(
      'INSERT INTO sync_estado (clave, valor) VALUES (?, ?) '
      'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
      [_clave, jsonEncode(impresora.aMapa())],
    );
    state = impresora;
  }

  void olvidar() {
    ref
        .read(baseLocalProvider)
        .db
        .execute('DELETE FROM sync_estado WHERE clave = ?', [_clave]);
    state = null;
  }
}
