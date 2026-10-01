/// Estado del tablero de Gerencia.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ NO ES UN `FutureProvider` PELADO
/// ─────────────────────────────────────────────────────────────────────────
/// Un `FutureProvider` modela bien "cargando / listo / error", y aquí hay un
/// cuarto estado que no cabe en esos tres y que es el más importante:
/// **cifras viejas de la copia local**. No es un error —hay cifras, y sirven—
/// ni es un "listo" normal, porque la pantalla tiene que decir de cuándo son.
///
/// Si se modelara como error, el gerente sin señal vería una pantalla roja
/// teniendo en el bolsillo las cifras de hace una hora. Si se modelara como
/// listo, las vería como si fueran de ahora. Las dos lecturas llevan a decidir
/// mal, y por eso el estado es explícito.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/repo_tablero.dart';
import 'sesion.dart';
import 'sincronizacion.dart';

/// Qué día se está viendo. Nulo es hoy.
final fechaDelTableroProvider = StateProvider<DateTime?>((_) => null);

final repoTableroProvider = Provider<RepoTablero?>((ref) {
  final transporte = ref.watch(transporteProvider);
  if (transporte == null) return null;
  return RepoTablero(
    ref.watch(baseLocalProvider).db,
    ClienteTablero(transporte),
    reloj: ref.watch(relojProvider),
  );
});

sealed class EstadoTablero {
  const EstadoTablero();
}

class TableroCargando extends EstadoTablero {
  const TableroCargando();
}

/// Cifras al día: se acaban de bajar del servidor.
class TableroListo extends EstadoTablero {
  const TableroListo(this.local);

  final TableroLocal local;
}

/// No hubo red, pero hay copia. La pantalla muestra las cifras CON su edad.
class TableroDeLaCopia extends EstadoTablero {
  const TableroDeLaCopia(this.local);

  final TableroLocal local;
}

/// Ni red ni copia. Es el único caso sin cifras que mostrar.
class TableroSinNada extends EstadoTablero {
  const TableroSinNada(this.motivo);

  final String motivo;
}

/// El servidor dijo que este usuario no puede ver el tablero.
///
/// Se distingue de todo lo demás porque no se arregla reintentando ni buscando
/// señal: hay que pedir el permiso en la oficina. Un botón de "volver a
/// intentar" en este caso es una invitación a perder el tiempo.
class TableroSinPermiso extends EstadoTablero {
  const TableroSinPermiso();
}

/// La sesión venció. Hay que volver a entrar.
class TableroSesionVencida extends EstadoTablero {
  const TableroSesionVencida();
}

/// Este teléfono nunca abrió sesión en línea.
///
/// No es lo mismo que "la sesión venció", y confundirlos manda a la persona a
/// resolver algo que no está roto. Pasa de verdad: un gerente o un admin que
/// entró con el PIN guardado de un equipo registrado tiene sesión en la app y
/// NO tiene token para hablar con el servidor. Decirle "venció" lo deja
/// buscando qué expiró.
class TableroSinSesionEnLinea extends EstadoTablero {
  const TableroSinSesionEnLinea();
}

/// Falla del servidor. Reintentar tiene sentido.
class TableroConError extends EstadoTablero {
  const TableroConError(this.mensaje);

  final String mensaje;
}

class ControladorTablero extends Notifier<EstadoTablero> {
  @override
  EstadoTablero build() {
    // No se dispara la carga desde `build`: un Notifier que lanza trabajo al
    // construirse se vuelve imposible de probar sin pelear con el reloj. La
    // pantalla llama a `cargar()` en su `initState`.
    return const TableroCargando();
  }

  Future<void> cargar() async {
    final repo = ref.read(repoTableroProvider);
    if (repo == null) {
      state = const TableroSinSesionEnLinea();
      return;
    }
    state = const TableroCargando();
    final fecha = ref.read(fechaDelTableroProvider);
    try {
      final local = await repo.ver(fecha: fecha);
      state = local.deLaCopia ? TableroDeLaCopia(local) : TableroListo(local);
    } on SinTableroNiCopia catch (e) {
      state = TableroSinNada(e.motivo);
    } on SinPermisoDeTablero {
      state = const TableroSinPermiso();
    } on SesionInvalida {
      state = const TableroSesionVencida();
    } on ServidorConProblemas catch (e) {
      state = TableroConError('El servidor contestó HTTP ${e.codigo}.');
    }
  }
}

final tableroProvider =
    NotifierProvider<ControladorTablero, EstadoTablero>(ControladorTablero.new);

// ---------------------------------------------------------------------------
// El mapa
// ---------------------------------------------------------------------------
sealed class EstadoMapa {
  const EstadoMapa();
}

class MapaCargando extends EstadoMapa {
  const MapaCargando();
}

class MapaListo extends EstadoMapa {
  const MapaListo(this.local);

  final MapaLocal local;
}

class MapaSinNada extends EstadoMapa {
  const MapaSinNada(this.motivo);

  final String motivo;
}

class ControladorMapa extends Notifier<EstadoMapa> {
  @override
  EstadoMapa build() => const MapaCargando();

  Future<void> cargar() async {
    final repo = ref.read(repoTableroProvider);
    if (repo == null) {
      state = const MapaSinNada('No hay sesión en línea.');
      return;
    }
    state = const MapaCargando();
    try {
      state = MapaListo(await repo.mapa(fecha: ref.read(fechaDelTableroProvider)));
    } on SinTableroNiCopia catch (e) {
      state = MapaSinNada(e.motivo);
    } on SinPermisoDeTablero {
      state = const MapaSinNada('Tu usuario no tiene permiso para ver el mapa.');
    } on SesionInvalida {
      state = const MapaSinNada('La sesión venció. Vuelve a entrar.');
    } on ServidorConProblemas catch (e) {
      state = MapaSinNada('El servidor contestó HTTP ${e.codigo}.');
    }
  }
}

final mapaProvider =
    NotifierProvider<ControladorMapa, EstadoMapa>(ControladorMapa.new);
