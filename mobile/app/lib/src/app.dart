/// Portal por rol.
///
/// La app cambia por completo según quién entra. No son dos pestañas del mismo
/// árbol: son dos shells distintos, y el de gerencia **no compila contra la
/// maquinaria offline**. Son dos apps que comparten binario.
library;

import 'dart:async';

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'estado/sesion.dart';
import 'marca.dart';
import 'estado/sincronizacion.dart';
import 'pantallas/gerencia/portal.dart';
import 'pantallas/equipo_dado_de_baja.dart';
import 'pantallas/login.dart';
import 'pantallas/sin_servidor.dart';
import 'pantallas/vendedor/clientes.dart';

class AppDsd extends ConsumerStatefulWidget {
  const AppDsd({super.key});

  @override
  ConsumerState<AppDsd> createState() => _EstadoAppDsd();
}

class _EstadoAppDsd extends ConsumerState<AppDsd> {
  /// Al volver a primer plano se mira el reloj otra vez (ADR 0002 §164):
  /// Android deja la app viva de un día para otro, y «hoy» se quedaba en el día
  /// en que se abrió.
  late final AppLifecycleListener _alVolver;

  /// La sincronización automática (ADR 0002 §169): «cada dos minutos», mientras
  /// la app está abierta, y al volver a ella. El vendedor hace su corte, pide su
  /// carga y no tiene que acordarse de sincronizar: se envía sola, y la carga que
  /// acepta la oficina le llega sola.
  Timer? _cadaRato;

  @override
  void initState() {
    super.initState();
    _alVolver = AppLifecycleListener(
      onResume: () {
        ref.read(diaOperativoProvider.notifier).revisar();
        _sincronizarSola();
      },
    );
    final cada = ref.read(intervaloDeSyncAutomaticaProvider);
    if (cada != null) {
      _cadaRato = Timer.periodic(cada, (_) => _sincronizarSola());
    }
  }

  /// En silencio, y solo con la app a la vista y una sesión con base en el
  /// teléfono (la del vendedor, o la del gerente que entró con su PIN). Sin
  /// señal no pasa nada: lo pendiente sale en la siguiente.
  void _sincronizarSola() {
    if (ref.read(intervaloDeSyncAutomaticaProvider) == null) return;
    final ahora = WidgetsBinding.instance.lifecycleState;
    if (ahora != null && ahora != AppLifecycleState.resumed) return;
    if (ref.read(sesionProvider) is! SesionAbierta) return;
    ref.read(syncProvider.notifier).sincronizar(silenciosa: true);
  }

  @override
  void dispose() {
    _cadaRato?.cancel();
    _alVolver.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: nombreDeLaEmpresa,
        debugShowCheckedModeBanner: false,
        theme: temaDeLaMarca(),
        home: const PortalPorRol(),
      );
}

class PortalPorRol extends ConsumerWidget {
  const PortalPorRol({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Antes que todo lo demás, incluido el borrado remoto: si el binario no
    // sabe a qué servidor hablar, ninguna de las pantallas siguientes puede
    // hacer su trabajo, y la de login mentiría llamándolo falta de señal.
    if (apkSinServidor) return const PantallaSinServidor();

    final sesion = ref.watch(sesionProvider);

    // El borrado remoto (Fase 9) gana a cualquier otra pantalla, incluida la de
    // login. Si no estuviera antes, un equipo ya borrado volvería a la pantalla
    // de PIN y la persona intentaría entrar una y otra vez contra una
    // credencial que ya no existe, sin que nada le dijera por qué.
    final sync = ref.watch(syncProvider);
    if (sync is SyncEquipoBorrado) {
      return PantallaEquipoBorrado(motivo: sync.motivo);
    }
    if (sync is SyncBorradoPendiente) {
      return PantallaEquipoBloqueado(
        pendientes: sync.pendientes,
        motivo: sync.motivo,
      );
    }

    return switch (sesion) {
      // Antes de pedir la contraseña, se intenta volver a entrar solo: ver
      // `vigenciaDeLaSesion`. Solo lee el Keystore, así que tarda un instante.
      SinSesion() => ref.watch(reaperturaProvider).isLoading
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : const PantallaLogin(),
      SesionAbierta(:final credencial) => _porRol(credencial),
      // Gerencia entró en línea y no guardó credencial: su teléfono no tiene
      // cartera ni maquinaria offline que proteger. Ver `SesionDeGerencia`.
      SesionDeGerencia(:final perfil) => PortalDeOficina(nombre: perfil.nombre),
    };
  }

  Widget _porRol(CredencialLocal credencial) => switch (credencial.rol) {
        'vendedor' || 'supervisor' => const PantallaClientes(),
        _ => PortalDeOficina(nombre: credencial.nombre),
      };
}
