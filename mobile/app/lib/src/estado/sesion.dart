/// Sesión y dependencias de la app.
library;

import 'dart:async';

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

/// Cambia cada vez que algo modifica el inventario del camión en la base local.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ EXISTE: «VENDO Y EL CAMIÓN NO BAJA» (octubre 2026)
/// ─────────────────────────────────────────────────────────────────────────
/// La venta SÍ descontaba la base local, en la misma transacción que el
/// documento. Lo que no bajaba era la PANTALLA: «Mi camión» se calculaba la
/// primera vez que se abría y nadie lo volvía a calcular. Cada lugar que cambia
/// el camión —la venta, la merma, la devolución, la sincronización— invalidaba
/// a mano SU lista de proveedores, y ninguna lista estaba completa: la venta no
/// tocaba «Mi camión», la sincronización no tocaba ninguno.
///
/// Con este contador, los que LEEN el camión lo observan y los que lo CAMBIAN
/// lo incrementan. Un lector nuevo no puede olvidar en qué lista apuntarse,
/// porque no hay lista: si lee el camión, observa el contador.
final revisionDelCamionProvider = StateProvider<int>((_) => 0);

/// Lo llama todo el que escriba `existencias_camion` o lo que de ella depende.
void elCamionCambio(Ref ref) =>
    ref.read(revisionDelCamionProvider.notifier).state++;

final repoCredencialProvider = Provider<RepoCredencial>(
  (ref) => RepoCredencial(ref.watch(almacenSeguroProvider)),
);

/// De quién es este teléfono, según la credencial que dejó la vinculación.
///
/// La pantalla de entrada lo muestra para que el vendedor no tenga que recordar
/// con qué código quedó vinculado su equipo —y para que la oficina vea de un
/// vistazo, con el teléfono en la mano, a quién pertenece sin entrar a nada.
///
/// `null` antes de vincular, y también cuando la credencial venció o se borró
/// por un borrado remoto: en los tres casos lo honesto es no decir ningún nombre.
final credencialGuardadaProvider = FutureProvider<CredencialLocal?>(
  (ref) => ref.watch(repoCredencialProvider).leer(),
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

    // Y, SI HAY RED, UN TOKEN — porque sin él entrar no sirve de nada.
    //
    // `tokenProvider` lo ponían solo `vincularEquipo` y los caminos de Gerencia.
    // Este, que es el de TODOS LOS DÍAS, no lo ponía: el vendedor entraba con su
    // contraseña, el token quedaba en null, y al sincronizar la app contestaba
    // «Entraste sin señal» —con señal de sobra— porque es literalmente lo que
    // ve: no hay token. La única forma de volver a sincronizar era vincular el
    // equipo otra vez, que sí hace login en línea.
    //
    // NO SE ESPERA a que termine, y eso es la parte importante: entrar es lo
    // primero que hace el vendedor en la bodega, muchas veces sin datos, y no
    // puede quedarse colgado hasta que una petición agote su tiempo límite. La
    // sesión ya está abierta arriba; esto solo añade la capacidad de sincronizar
    // cuando se pueda. Si falla, no pasa nada y «sin señal» será verdad.
    // Solo si no hay token ya. `vincularEquipo` termina llamando aquí y acaba de
    // conseguir uno: sin esta condición, vincular haría DOS logins en línea —dos
    // viajes y dos verificaciones Argon2 en el servidor por cada equipo— y lo
    // detectó una prueba que esperaba los folios como última llamada.
    if (resultado == ResultadoLogin.ok && ref.read(tokenProvider) == null) {
      unawaited(_conseguirTokenEnLinea(credencial!.codigo, password));
    }
    return resultado;
  }

  /// Login en línea silencioso, para dejar la sesión en condiciones de subir.
  ///
  /// Se traga cualquier error a propósito: nada de lo que pase aquí debe cambiar
  /// lo que el vendedor ve después de entrar. Si no hay red, se queda sin token y
  /// la pantalla de sincronización lo dirá con razón.
  Future<void> _conseguirTokenEnLinea(String codigo, String password) async {
    try {
      final fila = ref
          .read(baseLocalProvider)
          .db
          .select("SELECT valor FROM sync_estado WHERE clave = 'dispositivo_id'");
      if (fila.isEmpty) return;

      final sesion = await ClienteAuth(ref.read(transporteSinSesionProvider)).entrar(
        codigo: codigo,
        password: password,
        dispositivoId: fila.first['valor'] as String,
      );
      ref.read(tokenProvider.notifier).state = sesion.accessToken;
      await ref
          .read(almacenSeguroProvider)
          .escribir(claveRefreshToken, sesion.refreshToken);
    } on Object {
      // Sin red, o el servidor dijo que no. Se entró igual: es el punto.
    }
  }

  /// Vincula ESTE teléfono a su vendedor, la primera vez.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// EL PASO QUE FALTABA, Y POR QUÉ NO SE NOTABA
  /// ───────────────────────────────────────────────────────────────────────
  /// `entrarOffline` necesita una credencial ya guardada, y lo único que la
  /// guardaba era el sembrador del modo demo — que los dos cerrojos de
  /// compilación eliminan del binario de release. Así que en producción un
  /// vendedor no tenía NINGÚN camino para entrar: ni online (esa pantalla es de
  /// Gerencia y exige `puedeVerTablero`) ni sin señal (no hay credencial).
  ///
  /// Las piezas estaban las cinco escritas y ninguna conectada: el servidor
  /// devuelve `credencial_local` cuando el login trae `dispositivo_id`, el
  /// cliente Dart acepta el parámetro, `RepoCredencial.guardar` y
  /// `RepoFolios.guardar` existen, y `/dispositivos/{id}/folios` asigna rangos.
  /// Esto las une.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// EL ORDEN IMPORTA: PRIMERO LO QUE NO SE PUEDE REHACER SIN SEÑAL
  /// ───────────────────────────────────────────────────────────────────────
  /// La credencial y el `dispositivo_id` se guardan ANTES de pedir los folios. Si
  /// la red se corta en medio, el vendedor queda vinculado y puede entrar con su
  /// PIN —le faltarán folios y la pantalla de cobro lo dirá— en vez de quedar
  /// fuera de la app con la credencial a medio camino. Pedir folios se reintenta
  /// con señal; recuperar una credencial perdida, no.
  Future<String?> vincularEquipo({
    required String codigo,
    required String password,
    required String dispositivoId,
  }) async {
    final id = dispositivoId.trim();
    if (id.isEmpty) {
      return 'Falta el identificador del equipo. Lo da la oficina al vincularlo '
          'en el panel, en Teléfonos.';
    }

    final transporte = ref.read(transporteSinSesionProvider);
    final SesionEnLinea sesion;
    try {
      sesion = await ClienteAuth(transporte).entrar(
        codigo: codigo.trim(),
        password: password,
        dispositivoId: id,
      );
    } on CredencialesInvalidas {
      return 'Código o contraseña incorrectos.';
    } on LoginRechazado catch (e) {
      // Se devuelve el texto del SERVIDOR: dice «dispositivo no registrado» o
      // «el dispositivo pertenece a otro usuario», y traducirlo a un genérico
      // dejaría a quien lo lea intentando lo mismo otra vez.
      return e.detalle;
    } on ErrorDeRed catch (e) {
      return 'No se pudo conectar: ${e.mensaje}. Vincular el equipo necesita '
          'señal una sola vez; después entra con su PIN sin red.';
    } on ServidorConProblemas catch (e) {
      return 'El servidor contestó con un error (HTTP ${e.codigo}).';
    }

    final credencial = sesion.credencialCruda;
    if (credencial == null) {
      // El servidor solo manda credencial cuando el login trae un dispositivo
      // registrado. Sin ella no hay login offline, así que vincular no sirvió.
      return 'El servidor no devolvió credencial para este equipo. Revisa que '
          'esté vinculado a este vendedor en el panel, en Teléfonos.';
    }

    await ref.read(repoCredencialProvider).guardar(credencial);
    // El nombre de la pantalla de entrada se lee de aquí, y es un FutureProvider:
    // ya se evaluó como `null` al construirse la pantalla y Riverpod lo cachea.
    // Sin invalidarlo, el teléfono no diría de quién es hasta reiniciar la app.
    ref.invalidate(credencialGuardadaProvider);
    ref.read(baseLocalProvider).db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('dispositivo_id', ?) "
      'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
      [id],
    );
    ref.read(tokenProvider.notifier).state = sesion.accessToken;
    await ref
        .read(almacenSeguroProvider)
        .escribir(claveRefreshToken, sesion.refreshToken);

    // Los folios, que son lo que permite CERRAR una venta. Si fallan, el equipo
    // ya quedó vinculado: se avisa y se puede reintentar sincronizando.
    // Se usa `transporteProvider` y no un `TransporteHttp` armado a mano: ya
    // lleva el token que acabamos de poner, y así hay un solo lugar donde se
    // decide cómo se habla con el servidor.
    final conSesion = ref.read(transporteProvider);
    if (conSesion == null) {
      return 'El equipo quedó vinculado, pero no se pudieron traer los folios. '
          'Entra con tu PIN y sincroniza con señal antes de vender.';
    }
    try {
      final rangos = await ClienteDispositivo(conSesion).pedirFolios(id);
      final repo = RepoFolios(ref.read(baseLocalProvider).db);
      final ahora = ref.read(relojProvider)().toUtc().toIso8601String();
      for (final rango in rangos) {
        repo.guardar(rango, asignadoEn: ahora);
      }
    } on Object {
      return 'El equipo quedó vinculado, pero no se pudieron traer los folios. '
          'Entra con tu PIN y sincroniza con señal antes de vender.';
    }

    // Y ENTRAR, que es la mitad que faltaba y el botón ya prometía.
    //
    // Sin esto, vincular guardaba todo correctamente y la pantalla se limpiaba:
    // el vendedor volvía a la pantalla de entrada sin un mensaje, sin saber si
    // había funcionado, y con la contraseña correcta el resultado era
    // indistinguible de no haber hecho nada. (Con la contraseña MAL sí veía el
    // error, lo que lo hacía más desconcertante todavía.)
    //
    // Se reusa `entrarOffline` en vez de poner `state` a mano: es el mismo camino
    // de todos los días, así que verifica la credencial que acabamos de guardar
    // contra la contraseña que el vendedor acaba de teclear. Si eso no cuadrara,
    // la credencial no serviría mañana y es mejor saberlo ahora que a las 6 am.
    final entro = await entrarOffline(password);
    if (entro != ResultadoLogin.ok) {
      return 'El equipo quedó vinculado, pero no se pudo abrir la sesión '
          '($entro). Intenta entrar con tu contraseña en la pantalla de entrada.';
    }

    return null;
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

/// El camión del vendedor: lo que el servidor publicó, o lo que trae la credencial.
///
/// ───────────────────────────────────────────────────────────────────────────
/// EL ORDEN IMPORTA, Y ES EL CONTRARIO DEL OBVIO
/// ───────────────────────────────────────────────────────────────────────────
/// La credencial se reescribe solo cuando el vendedor entra **con señal**, así que
/// es el dato más VIEJO de los dos: si la oficina le reasignó el camión mientras
/// trabajaba offline, la credencial sigue diciendo el anterior y el delta ya trae
/// el nuevo. Preferir el delta es preferir lo que el servidor sabe hoy.
///
/// La credencial queda como respaldo para el teléfono que todavía no ha recibido
/// ningún delta de identidad —el recién vinculado—, y porque el día que el
/// servidor no publique nada, un camión viejo es mejor que ninguno: sin
/// `almacen_id` el vendedor no puede ni vender ni mermar.
final almacenDelVendedorProvider = Provider<String?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;

  final db = ref.watch(baseLocalProvider).db;
  final filas = db.select(
    "SELECT valor FROM sync_estado WHERE clave = 'almacen_asignado'",
  );
  final asignado = filas.isEmpty ? null : filas.single['valor'] as String?;
  return asignado ?? sesion.credencial.almacenId;
});
