/// Estado de la sección Vendedores del portal de la oficina.
///
/// Como las cargas: vive en el servidor y no tiene copia en el teléfono. Cada
/// pantalla pregunta y pinta lo que le contestan.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'sesion.dart';
import 'sincronizacion.dart';

final clienteVendedoresProvider = Provider<ClienteVendedores?>((ref) {
  final transporte = ref.watch(transporteProvider);
  return transporte == null ? null : ClienteVendedores(transporte);
});

/// Si se muestra el botón de Vendedores. La UI oculta; el servidor prohíbe.
final puedeVerVendedoresProvider = Provider<bool>(
  (ref) => switch (ref.watch(sesionProvider)) {
    SesionDeGerencia(:final perfil) => perfil.puedeVerVendedores,
    SesionAbierta(:final credencial) => credencial.puede('ventas.ver_todas'),
    SinSesion() => false,
  },
);

/// El error, dicho para quien tiene el teléfono en la mano. Sirve para las
/// pantallas de la oficina: vendedores y cargas.
String explicarErrorDeOficina(Object error) => switch (error) {
      ServidorSinEstaFuncion() =>
        'El servidor todavía no tiene esta función. Hay que actualizarlo '
            '(git pull y docker compose) y volver a intentar.',
      CargaRechazada(:final detalle) => detalle,
      SinPermisoDeCargar() =>
        'Tu usuario no tiene permiso de cargar camiones. Se da desde el panel.',
      SinPermisoDeVendedores() =>
        'Tu usuario no tiene permiso de ver a los vendedores (ventas.ver_todas).',
      SinPermisoDeCortar() =>
        'Tu usuario no tiene permiso de hacer el corte del día. Se da desde el panel.',
      SinPermisoDeAlmacen() =>
        'Tu usuario no tiene permiso para esto del almacén (inventario.ajustar). '
            'Se da desde el panel.',
      SesionInvalida() => 'La sesión venció. Sal y vuelve a entrar.',
      ErrorDeRed(:final mensaje) =>
        'No se pudo conectar ($mensaje). Esta pantalla necesita señal.',
      ServidorConProblemas(:final codigo) =>
        'El servidor contestó con un error (HTTP $codigo).',
      _ => '$error',
    };
