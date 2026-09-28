/// Estado de la sincronización en la app.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/transporte_http.dart';
import 'sesion.dart';

/// Dirección del servidor local. Se configura por entorno; en producción apunta
/// al hostname del túnel de Cloudflare.
final baseUrlProvider = Provider<String>((_) => 'https://api.localhost');

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
