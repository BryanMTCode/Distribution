/// Estado de las cargas del camión, para el portal de la oficina.
///
/// Es poco a propósito: las cargas viven en el servidor y no tienen copia en el
/// teléfono. Cada pantalla le pregunta al servidor y pinta lo que contesta —que
/// siempre trae la carga completa, con el mensaje de lo último que se hizo—.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'sesion.dart';
import 'sincronizacion.dart';

final clienteCargasProvider = Provider<ClienteCargas?>((ref) {
  final transporte = ref.watch(transporteProvider);
  return transporte == null ? null : ClienteCargas(transporte);
});

/// Si se muestra el botón de cargas. La UI oculta; el servidor prohíbe.
///
/// Admin, supervisor y gerente lo tienen (`inventario.cargar`). El vendedor no:
/// en su teléfono el botón ni existe.
final puedeCargarProvider = Provider<bool>(
  (ref) => switch (ref.watch(sesionProvider)) {
    SesionDeGerencia(:final perfil) => perfil.puedeCargar,
    SesionAbierta(:final credencial) => credencial.puede('inventario.cargar'),
    SinSesion() => false,
  },
);

/// El error, dicho para quien está en la bodega con el teléfono en la mano.
String explicarErrorDeCarga(Object error) => switch (error) {
      CargaRechazada(:final detalle) => detalle,
      SinPermisoDeCargar() =>
        'Tu usuario no tiene permiso de cargar camiones. Se da desde el panel.',
      SesionInvalida() => 'La sesión venció. Sal y vuelve a entrar.',
      ErrorDeRed(:final mensaje) =>
        'No se pudo conectar ($mensaje). Cargar el camión necesita señal.',
      ServidorConProblemas(:final codigo) =>
        'El servidor contestó con un error (HTTP $codigo).',
      _ => '$error',
    };
