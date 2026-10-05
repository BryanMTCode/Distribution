/// Estado de la sincronización en la app.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/transporte_http.dart';
import 'sesion.dart';

/// Dirección del servidor.
///
/// Se fija en **tiempo de compilación** con `--dart-define=DSD_BASE_URL=...`, no
/// en una pantalla de ajustes. Un campo editable para apuntar el teléfono a otro
/// servidor es un camino para que un equipo robado mande la cartera a donde
/// quiera quien lo tenga; y además nadie lo cambia nunca en operación normal.
///
/// En producción apunta al hostname del túnel de Cloudflare. Para probar contra
/// la PC en la red local, el valor es la IP de la PC:
///
///     flutter run --dart-define=DSD_BASE_URL=http://192.168.1.50:8000
///
/// Con `http://` hace falta además el `usesCleartextTraffic` del manifiesto de
/// **debug** (está puesto ahí y solo ahí: en release Android lo prohíbe, y está
/// bien que lo prohíba).
/// Lo que queda cuando NADIE pasó `--dart-define=DSD_BASE_URL`.
///
/// Es un marcador, no un servidor: `api.localhost` no resuelve a ninguna parte.
/// Tiene nombre propio para que [apkSinServidor] lo compare contra ESTA
/// constante y no contra una cadena escrita dos veces — dos copias del mismo
/// valor literal se separan en cuanto alguien cambia una.
const marcadorSinServidor = 'https://api.localhost';

const baseUrlPorOmision = String.fromEnvironment(
  'DSD_BASE_URL',
  defaultValue: marcadorSinServidor,
);

/// `true` cuando este APK **de producción** se compiló sin decirle a qué
/// servidor hablar.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ HACE FALTA UNA PANTALLA Y NO BASTA CON `make apk`
/// ─────────────────────────────────────────────────────────────────────────
/// `make apk` exige `DSD_BASE_URL` y se niega sin ella, así que el camino
/// normal no puede producir este APK. Pero `flutter build apk --release` a
/// secas sí, y compila sin una queja: el binario queda apuntando a
/// `api.localhost`, se instala bien, abre bien, y el login falla con un error
/// de red.
///
/// Ese es el problema. «No hay internet» es lo que el vendedor concluye —y lo
/// que va a reportar— porque es lo que la pantalla de login diría. Alguien
/// pasaría la mañana revisando el túnel de Cloudflare, el router y la señal del
/// teléfono, buscando una falla que no está ahí. El binario es el que está mal
/// construido, y solo el binario puede decirlo.
///
/// Las dos partes son `const`, así que en depuración el compilador de Dart
/// elimina esta rama del árbol: `flutter run` sin define sigue funcionando
/// —apunta a `api.localhost`, que es lo correcto para el modo demo— y las
/// pruebas de widget no se enteran.
const apkSinServidor = kReleaseMode && baseUrlPorOmision == marcadorSinServidor;

final baseUrlProvider = Provider<String>((_) => baseUrlPorOmision);

/// Token vigente. Nulo mientras no haya sesión.
final tokenProvider = StateProvider<String?>((_) => null);

/// Cursor del último delta aplicado. Vive en `sync_estado` de la base local.
final cursorProvider = Provider<int>((ref) {
  final db = ref.watch(baseLocalProvider).db;
  final filas = db.select(
    "SELECT valor FROM sync_estado WHERE clave = 'cursor_pull'",
  );
  if (filas.isEmpty) return 0;
  return int.tryParse(filas.first['valor'] as String? ?? '0') ?? 0;
});

/// Transporte sin token, para el login: todavía no hay sesión que lo dé.
///
/// `token: ''` hace que `TransporteHttp` **omita** la cabecera, que no es lo
/// mismo que mandarla vacía: un `Authorization: Bearer ` sin valor es una
/// credencial mal formada y el servidor contesta 401 antes de llegar al
/// endpoint, tapando el 400 explicativo del login que sí sirve de mensaje.
final transporteSinSesionProvider = Provider<Transporte>(
  (ref) => TransporteHttp(baseUrl: ref.watch(baseUrlProvider), token: ''),
);

final transporteProvider = Provider<Transporte?>((ref) {
  final token = ref.watch(tokenProvider);
  if (token == null) return null;
  return TransporteHttp(baseUrl: ref.watch(baseUrlProvider), token: token);
});

/// Lo que la pantalla muestra del último intento.
sealed class EstadoSync {
  const EstadoSync();
}

class SyncInactiva extends EstadoSync {
  const SyncInactiva();
}

class SyncEnCurso extends EstadoSync {
  const SyncEnCurso();
}

class SyncTerminada extends EstadoSync {
  const SyncTerminada(this.resultado);

  final ResultadoSincronizacion resultado;
}

/// Sin token no se puede sincronizar: el vendedor entró offline y todavía no ha
/// tenido señal. No es un error — es el caso normal a media ruta.
class SyncSinSesionEnLinea extends EstadoSync {
  const SyncSinSesionEnLinea();
}

/// La oficina ordenó borrar este equipo y el teléfono YA LO HIZO (Fase 9).
///
/// Es el final del camino para esta instalación: no hay base, no hay
/// credencial, no hay nada que mostrar. La pantalla lo dice y no ofrece salida,
/// porque no hay ninguna que dar desde el teléfono.
class SyncEquipoBorrado extends EstadoSync {
  const SyncEquipoBorrado(this.motivo);

  final String? motivo;
}

/// Hay orden de borrado y todavía queda cola por entregar.
///
/// NO se borró nada. El equipo queda bloqueado mostrando cuántas operaciones le
/// faltan por subir: un equipo bloqueado con datos dentro es recuperable, uno
/// borrado no.
class SyncBorradoPendiente extends EstadoSync {
  const SyncBorradoPendiente({required this.pendientes, this.motivo});

  final int pendientes;
  final String? motivo;
}

class ControladorSync extends Notifier<EstadoSync> {
  @override
  EstadoSync build() => const SyncInactiva();

  /// Devuelve a la cola lo que quedó con error y vuelve a sincronizar.
  ///
  /// Existe porque un sobre en cuarentena no se reintenta nunca, y eso es
  /// correcto cuando el rechazo fue por los datos y desastroso cuando fue por un
  /// fallo nuestro: un `lote_id` mal formado dejó ventas válidas sin poder subir
  /// con el bug ya arreglado. El vendedor no tiene por qué saber la diferencia;
  /// lo que necesita es poder volver a intentarlo.
  Future<int> reintentarLoQueFallo() async {
    final cuantos = ref.read(outboxProvider).reencolarCuarentena();
    if (cuantos > 0) {
      ref.invalidate(resumenColaProvider);
      await sincronizar();
    }
    return cuantos;
  }

  Future<void> sincronizar() async {
    final transporte = ref.read(transporteProvider);
    if (transporte == null) {
      state = const SyncSinSesionEnLinea();
      return;
    }

    state = const SyncEnCurso();
    final db = ref.read(baseLocalProvider).db;
    // El reloj se resuelve antes de entrar a la parte asíncrona: leerlo desde
    // un callback que corre a mitad de la sincronización lo ata al ciclo de
    // vida del provider, y la sincronización puede sobrevivir a la pantalla.
    final reloj = ref.read(relojProvider);

    final sincronizador = Sincronizador(
      outbox: ref.read(outboxProvider),
      cliente: ClienteSync(transporte),
      aplicador: AplicadorDeltas(db),
      ahora: () => reloj().toUtc().toIso8601String(),
    );

    final resultado = await sincronizador.sincronizar(
      cursorActual: ref.read(cursorProvider),
    );

    // El cursor se guarda DESPUÉS de aplicar. Si la app muere entre el pull y
    // esta escritura, la próxima corrida vuelve a traer el mismo tramo:
    // aplicar dos veces es inofensivo, perderse un tramo no lo es.
    db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('cursor_pull', ?) "
      'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
      ['${resultado.cursor}'],
    );
    db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('ultima_sync_ok', ?) "
      'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
      [reloj().toUtc().toIso8601String()],
    );

    // Si la sesión caducó, el vendedor tiene que volver a entrar: se limpia el
    // token para que la app no siga intentando con uno muerto.
    if (resultado.fin == FinDeSync.sesionInvalida) {
      ref.read(tokenProvider.notifier).state = null;
    }

    ref.invalidate(resumenColaProvider);
    ref.invalidate(clientesProvider);
    ref.invalidate(cursorProvider);

    // ---- Orden de borrado (Fase 9) --------------------------------------
    //
    // Se atiende al final, después de guardar el cursor y la hora: si la app
    // muriera a media secuencia, lo ya entregado queda registrado igual.
    final ordenes = resultado.ordenes;
    if (ordenes != null && ordenes.borrar) {
      if (resultado.fin == FinDeSync.borradoListo) {
        state = await _borrarEsteEquipo(
          cliente: ClienteSync(transporte),
          motivo: ordenes.borradoMotivo,
        );
        return;
      }
      // Hay orden y queda cola: NO se borra nada. La regla completa está en la
      // migración 0023 — nunca se borra lo que no se ha entregado.
      state = SyncBorradoPendiente(
        pendientes: ref.read(outboxProvider).resumen().pendientes,
        motivo: ordenes.borradoMotivo,
      );
      return;
    }

    state = SyncTerminada(resultado);
  }

  /// Borra la base y la credencial, y recién entonces lo confirma.
  ///
  /// ───────────────────────────────────────────────────────────────────────
  /// EL ORDEN DE ESTAS TRES COSAS ES LA PARTE DELICADA
  /// ───────────────────────────────────────────────────────────────────────
  /// 1. **La credencial primero.** Es lo que permite entrar sin red. Si se
  ///    cortara la luz entre el paso 1 y el 2, queda un teléfono con datos y
  ///    sin forma de abrirlos — incómodo pero seguro. Al revés (base primero,
  ///    credencial después) quedaría un teléfono que todavía entra, a una base
  ///    que ya no existe, y la app reventaría al abrir.
  /// 2. **La base después.** Se borra el archivo, no las tablas.
  /// 3. **La confirmación al final, y si falla no se deshace nada.** El
  ///    teléfono ya está limpio; la oficina lo verá como «orden sin confirmar»,
  ///    que es el lado correcto del que equivocarse. Confirmar ANTES dejaría al
  ///    servidor creyendo que el equipo está limpio cuando aún tiene todo.
  Future<EstadoSync> _borrarEsteEquipo({
    required ClienteSync cliente,
    required String? motivo,
  }) async {
    await ref.read(repoCredencialProvider).olvidar();
    // Y que la pantalla de entrada deje de decir de quién era: un equipo borrado
    // en remoto no debe seguir anunciando a su vendedor.
    ref.invalidate(credencialGuardadaProvider);
    await ref.read(almacenSeguroProvider).borrar('llave_base_local');
    await ref.read(almacenSeguroProvider).borrar(claveRefreshToken);
    ref.read(baseLocalProvider).borrarTodo();

    try {
      await cliente.confirmarBorrado(colaPendiente: 0);
    } on Exception {
      // Da igual por qué falló: no hay nada que reintentar desde aquí, porque
      // ya no hay credencial con la que volver a entrar. La oficina lo ve como
      // pendiente de confirmar y decide.
    }

    ref.read(tokenProvider.notifier).state = null;
    return SyncEquipoBorrado(motivo);
  }
}

final syncProvider = NotifierProvider<ControladorSync, EstadoSync>(
  ControladorSync.new,
);
