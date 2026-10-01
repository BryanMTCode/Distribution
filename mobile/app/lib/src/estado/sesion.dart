/// Sesión y dependencias de la app.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/almacen_seguro.dart';
import '../datos/base_local.dart';
import '../datos/repo_clientes.dart';
import '../datos/repo_credencial.dart';
import 'sincronizacion.dart';

/// Dónde vive el refresh token de Gerencia en el Keystore.
const claveRefreshToken = 'refresh_token_gerencia';

/// Se sobrescriben en las pruebas y en el arranque real.
final almacenSeguroProvider = Provider<AlmacenSeguro>(
  (_) => throw UnimplementedError('se define en el arranque'),
);
final baseLocalProvider = Provider<BaseLocal>(
  (_) => throw UnimplementedError('se define en el arranque'),
);

final repoCredencialProvider = Provider<RepoCredencial>(
  (ref) => RepoCredencial(ref.watch(almacenSeguroProvider)),
);
final repoClientesProvider = Provider<RepoClientes>(
  (ref) => RepoClientes(ref.watch(baseLocalProvider).db),
);
final outboxProvider = Provider<Outbox>(
  (ref) => Outbox(ref.watch(baseLocalProvider).db),
);

/// Reloj inyectable: las pruebas de vigencia necesitan mover el tiempo, y usar
/// `DateTime.now()` directo las volvería dependientes del día en que corren.
final relojProvider = Provider<DateTime Function()>((_) => DateTime.now);

/// Estado de la sesión.
sealed class Sesion {
  const Sesion();
}

class SinSesion extends Sesion {
  const SinSesion({this.motivo});

  /// Por qué no hay sesión. Se muestra al vendedor: "conéctate para renovar"
  /// es muy distinto de "PIN incorrecto".
  final ResultadoLogin? motivo;
}

class SesionAbierta extends Sesion {
  const SesionAbierta(this.credencial);

  final CredencialLocal credencial;
}

/// Sesión de un perfil que NO opera offline: Gerencia.
///
/// Es un estado aparte y no un `SesionAbierta` con credencial nula, por una
/// razón que importa: `SesionAbierta` implica que hay una credencial guardada
/// en el Keystore y que el equipo puede volver a entrar sin red. Un teléfono de
/// gerencia no guarda cartera ni credencial —no le hace falta y no debería—, y
/// confundir los dos casos haría que algún día una pantalla del vendedor
/// intentara leer una credencial que no existe.
///
/// El token vive aquí, en memoria del proceso. Lo que se guarda en el Keystore
/// es el refresh token, para que abrir la app por la mañana no exija teclear la
/// contraseña otra vez.
class SesionDeGerencia extends Sesion {
  const SesionDeGerencia(this.perfil);

  final Perfil perfil;
}

class ControladorSesion extends Notifier<Sesion> {
  @override
  Sesion build() => const SinSesion();

  /// Login sin señal contra la credencial guardada.
  ///
  /// Es el camino normal: el vendedor arranca su día en la bodega, muchas veces
  /// sin datos. El login online solo hace falta la primera vez y cuando la
  /// credencial vence.
  Future<ResultadoLogin> entrarOffline(String password) async {
    final credencial = await ref.read(repoCredencialProvider).leer();
    final resultado = intentarLoginOffline(
      credencial,
      password,
      ahora: ref.read(relojProvider)(),
    );

    state = resultado == ResultadoLogin.ok
        ? SesionAbierta(credencial!)
        : SinSesion(motivo: resultado);
    return resultado;
  }

  /// Abre la sesión de Gerencia contra el servidor.
  ///
  /// No guarda credencial para login offline: ver `SesionDeGerencia`. Lo que
  /// guarda es el refresh token, y solo ese.
  ///
  /// Devuelve `null` si entró; si no, el mensaje que hay que mostrar. Se
  /// devuelve el texto del SERVIDOR cuando lo hay —"un vendedor debe iniciar
  /// sesión desde un dispositivo registrado"— porque traducirlo a un genérico
  /// dejaría a quien lo lea intentando lo mismo otra vez.
  Future<String?> entrarEnLinea({
    required String codigo,
    required String password,
  }) async {
    final transporte = ref.read(transporteSinSesionProvider);
    try {
      final sesion = await ClienteAuth(transporte).entrar(
        codigo: codigo.trim(),
        password: password,
      );
      final perfil = sesion.perfil;
      if (perfil == null) {
        return 'El servidor no dijo quién entró. Puede estar en una versión '
            'más vieja que esta app.';
      }
      if (!perfil.puedeVerTablero) {
        // No es un error del servidor: el login SÍ funcionó. Pero esta app no
        // tiene ninguna pantalla que ofrecerle a este perfil, y meterlo a un
        // tablero que el servidor va a contestar con 403 en cada tarjeta sería
        // dejarlo adivinando.
        return 'Tu usuario entró, pero no tiene permiso para ver el tablero. '
            'Pídelo en la oficina.';
      }

      ref.read(tokenProvider.notifier).state = sesion.accessToken;
      await ref
          .read(almacenSeguroProvider)
          .escribir(claveRefreshToken, sesion.refreshToken);
      state = SesionDeGerencia(perfil);
      return null;
    } on CredencialesInvalidas {
      return 'Código o contraseña incorrectos.';
    } on LoginRechazado catch (e) {
      return e.detalle;
    } on ErrorDeRed catch (e) {
      return 'No se pudo conectar: ${e.mensaje}. El tablero necesita señal '
          'para consultar al servidor.';
    } on ServidorConProblemas catch (e) {
      return 'El servidor contestó con un error (HTTP ${e.codigo}).';
    }
  }

  /// Reabre la sesión de Gerencia con el refresh token guardado.
  ///
  /// Es lo que evita teclear la contraseña cada mañana. Si falla —token
  /// revocado, sin señal— no se trata como error: simplemente no hay sesión y
  /// se pide entrar, que es lo que la pantalla ya sabe mostrar.
  Future<bool> reabrirGerencia() async {
    final guardado =
        await ref.read(almacenSeguroProvider).leer(claveRefreshToken);
    if (guardado == null) return false;
    try {
      final sesion =
          await ClienteAuth(ref.read(transporteSinSesionProvider)).renovar(guardado);
      final perfil = sesion.perfil;
      if (perfil == null || !perfil.puedeVerTablero) return false;
      ref.read(tokenProvider.notifier).state = sesion.accessToken;
      state = SesionDeGerencia(perfil);
      return true;
    } on Exception {
      return false;
    }
  }

  Future<void> salir() async {
    // El refresh token se borra al salir: dejarlo haría que "salir" no sacara a
    // nadie —la siguiente apertura reabriría la sesión sola— y eso convierte un
    // botón de seguridad en un adorno.
    await ref.read(almacenSeguroProvider).borrar(claveRefreshToken);
    ref.read(tokenProvider.notifier).state = null;
    state = const SinSesion();
  }
}

final sesionProvider = NotifierProvider<ControladorSesion, Sesion>(
  ControladorSesion.new,
);

/// Lista de clientes filtrada. Lee de SQLite, así que responde igual sin señal.
final busquedaClientesProvider = StateProvider<String>((_) => '');

final clientesProvider = Provider<List<ClienteEnRuta>>((ref) {
  final busqueda = ref.watch(busquedaClientesProvider);
  return ref.watch(repoClientesProvider).deLaRuta(busqueda: busqueda);
});

/// Lo que hay sin sincronizar. Alimenta el indicador que el vendedor ve
/// siempre: un número de pendientes que crece es la señal de que algo va mal.
final resumenColaProvider = Provider<ResumenCola>(
  (ref) => ref.watch(outboxProvider).resumen(),
);
