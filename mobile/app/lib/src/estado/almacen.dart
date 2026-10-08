/// Estado del almacén para el portal de la oficina: existencias, entradas y
/// traspasos entre bodegas.
///
/// Como las cargas y el corte, vive en el servidor y no tiene copia en el
/// teléfono: cada pantalla pregunta y pinta lo que el servidor contesta.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'sesion.dart';
import 'sincronizacion.dart';

final clienteAlmacenProvider = Provider<ClienteAlmacen?>((ref) {
  final transporte = ref.watch(transporteProvider);
  return transporte == null ? null : ClienteAlmacen(transporte);
});

/// Los roles que ven TODOS los almacenes. El vendedor tiene `inventario.ver`
/// para su camión, no para la bodega ni para los camiones de los demás: es la
/// misma regla que `ROLES_DE_OFICINA` en el servidor.
const _rolesDeOficina = {'admin', 'gerente', 'supervisor'};

/// Si se muestran las existencias, las entradas y los traspasos. La UI oculta;
/// el servidor prohíbe.
final puedeVerAlmacenProvider = Provider<bool>(
  (ref) => switch (ref.watch(sesionProvider)) {
    SesionDeGerencia(:final perfil) =>
      _rolesDeOficina.contains(perfil.rol) && perfil.puede('inventario.ver'),
    SesionAbierta(:final credencial) =>
      _rolesDeOficina.contains(credencial.rol) && credencial.puede('inventario.ver'),
    SinSesion() => false,
  },
);

/// Si se puede recibir mercancía y traspasar (`inventario.ajustar`: admin,
/// supervisor, gerente). Sin él, las listas se ven pero sin botón de «nueva».
final puedeAjustarProvider = Provider<bool>(
  (ref) => switch (ref.watch(sesionProvider)) {
    SesionDeGerencia(:final perfil) => perfil.puede('inventario.ajustar'),
    SesionAbierta(:final credencial) => credencial.puede('inventario.ajustar'),
    SinSesion() => false,
  },
);
