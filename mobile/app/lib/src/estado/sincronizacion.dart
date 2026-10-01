/// Estado de la sincronización en la app.
library;

import 'package:dsd_core/dsd_core.dart';
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
const baseUrlPorOmision = String.fromEnvironment(
  'DSD_BASE_URL',
  defaultValue: 'https://api.localhost',
);

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

class ControladorSync extends Notifier<EstadoSync> {
  @override
  EstadoSync build() => const SyncInactiva();

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
    state = SyncTerminada(resultado);
  }
}

final syncProvider = NotifierProvider<ControladorSync, EstadoSync>(
  ControladorSync.new,
);
