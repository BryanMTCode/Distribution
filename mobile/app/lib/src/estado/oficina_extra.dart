/// Objetivos y mayoreo en el portal de la oficina (ADR 0002 §104 y §105).
///
/// Como las cargas: viven en el servidor y no tienen copia en el teléfono. Cada
/// pantalla pregunta y pinta lo que contesta.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'sesion.dart';
import 'sincronizacion.dart';

final clienteObjetivosProvider = Provider<ClienteObjetivos?>((ref) {
  final transporte = ref.watch(transporteProvider);
  return transporte == null ? null : ClienteObjetivos(transporte);
});

final clienteMayoreoProvider = Provider<ClienteMayoreo?>((ref) {
  final transporte = ref.watch(transporteProvider);
  return transporte == null ? null : ClienteMayoreo(transporte);
});

/// Si se muestra Mayoreo (`mayoreo.vender`, el admin). La UI oculta; el
/// servidor prohíbe.
final puedeVenderMayoreoProvider = Provider<bool>(
  (ref) => switch (ref.watch(sesionProvider)) {
    SesionDeGerencia(:final perfil) => perfil.puede('mayoreo.vender'),
    SesionAbierta() => false,
    SinSesion() => false,
  },
);
