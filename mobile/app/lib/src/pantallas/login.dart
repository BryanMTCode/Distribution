/// Login. Dos caminos que no se parecen, y a propósito.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL VENDEDOR ENTRA SIN SEÑAL; GERENCIA NO PUEDE
/// ─────────────────────────────────────────────────────────────────────────
/// El vendedor teclea un PIN que se verifica **en el teléfono** contra el hash
/// Argon2id guardado en el Keystore. Es el camino normal: arranca su día en la
/// bodega, muchas veces sin datos, y la venta tiene que poder ocurrir igual.
///
/// Gerencia es lo contrario y no hay forma de que no lo sea: el tablero existe
/// para ver lo que están haciendo los OTROS, y eso no se puede saber sin
/// preguntarle al servidor. Así que entra en línea, con código y contraseña, y
/// su teléfono no guarda credencial para entrar sin red — no le serviría, y un
/// teléfono de oficina no tiene por qué cargar con una.
///
/// Lo que sí se guarda es el refresh token. Volver a entrar sin teclear nada,
/// mientras la sesión no venza, lo hace `ControladorSesion.reabrir` antes de
/// que esta pantalla se muestre.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/servicio_ubicacion.dart';
import '../demo.dart';
import '../estado/alta.dart';
import '../estado/sesion.dart';
import '../estado/sincronizacion.dart';
import '../marca.dart';

class PantallaLogin extends ConsumerStatefulWidget {
  const PantallaLogin({super.key});

  @override
  ConsumerState<PantallaLogin> createState() => _EstadoLogin();
}

class _EstadoLogin extends ConsumerState<PantallaLogin> {
  final _pin = TextEditingController();
  final _codigo = TextEditingController();
  final _password = TextEditingController();
  final _equipo = TextEditingController();
  bool _verificando = false;
  bool _mostrarGerencia = false;
  ResultadoLogin? _error;

  /// Mensaje del camino en línea. Es texto y no un enum porque el que más
  /// importa lo escribe el SERVIDOR —"un vendedor debe iniciar sesión desde un
  /// dispositivo registrado"— y traducirlo a un genérico dejaría a quien lo lea
  /// intentando lo mismo otra vez.
  String? _errorEnLinea;
  String? _errorVinculo;
  bool _mostrarVinculo = false;

  @override
  void dispose() {
    _pin.dispose();
    _codigo.dispose();
    _password.dispose();
    _equipo.dispose();
    super.dispose();
  }

  Future<void> _entrarComoGerencia() async {
    setState(() {
      _verificando = true;
      _errorEnLinea = null;
      _error = null;
    });
    final problema = await ref.read(sesionProvider.notifier).entrarEnLinea(
          codigo: _codigo.text,
          password: _password.text,
        );
    if (!mounted) return;
    setState(() {
      _verificando = false;
      _errorEnLinea = problema;
    });
  }

  /// Vincula el teléfono la primera vez. Pide señal una sola vez en su vida.
  Future<void> _vincular() async {
    setState(() {
      _verificando = true;
      _errorVinculo = null;
      _error = null;
    });
    final problema = await ref.read(sesionProvider.notifier).vincularEquipo(
          codigo: _codigo.text,
          password: _password.text,
          claveDelEquipo: _equipo.text,
        );
    if (!mounted) return;
    setState(() {
      _verificando = false;
      _errorVinculo = problema;
      // Vinculado: se limpia la contraseña y se deja el PIN a la vista, que es
      // el camino de todos los días a partir de ahora.
      if (problema == null) {
        _password.clear();
        _equipo.clear();
        _mostrarVinculo = false;
      }
    });
  }

  Future<void> _entrar() async {
    // Verificar cuesta cientos de milisegundos a propósito (Argon2id con 64 MiB):
    // es lo que hace caro probar PINs en un teléfono robado. Se bloquea el botón
    // para que no se disparen varias verificaciones a la vez.
    setState(() {
      _verificando = true;
      _error = null;
    });
    final resultado = await ref.read(sesionProvider.notifier).entrarOffline(_pin.text);
    if (!mounted) return;
    setState(() {
      _verificando = false;
      _error = resultado == ResultadoLogin.ok ? null : resultado;
    });
  }

  /// Siembra la sesión y los datos de demostración, y entra.
  ///
  /// No es un atajo que salte la verificación: guarda la credencial y después
  /// llama al **mismo** login offline con el PIN de demo, así que el camino de
  /// Argon2 se recorre igual que en producción.
  Future<void> _entrarEnModoDemo() async {
    setState(() {
      _verificando = true;
      _error = null;
    });

    final ahora = ref.read(relojProvider)();
    await ref
        .read(repoCredencialProvider)
        .guardar(credencialDemo(ahora: ahora));

    // Se intenta leer el GPS para colocar los clientes de demostración
    // alrededor del punto donde estás: es lo que permite probar el aviso de
    // posible duplicado en campo. Si no hay lectura, se usan coordenadas fijas.
    final lectura = await ref.read(servicioUbicacionProvider).leer();
    sembrarDemo(
      ref.read(baseLocalProvider).db,
      referencia: lectura is GpsObtenido ? lectura.ubicacion : null,
      ahora: ahora.toUtc().toIso8601String(),
    );

    ref.invalidate(clientesProvider);
    ref.invalidate(resumenColaProvider);

    final resultado =
        await ref.read(sesionProvider.notifier).entrarOffline(pinDemo);
    if (!mounted) return;
    setState(() {
      _verificando = false;
      _error = resultado == ResultadoLogin.ok ? null : resultado;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const LogoDistribucionesSE(),

                  // De quién es este teléfono. Sale de la credencial que dejó la
                  // vinculación, así que aparece sola en cuanto el equipo queda
                  // vinculado y desaparece si la credencial vence o se borra en
                  // remoto. Sin ella no se inventa un nombre: la pantalla queda
                  // como estaba.
                  ...switch (ref.watch(credencialGuardadaProvider)) {
                    AsyncData(value: final c?) => [
                        const SizedBox(height: 8),
                        Text(
                          c.nombre,
                          key: const Key('etiqueta_vendedor'),
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                        Text(
                          c.codigo,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                    _ => const <Widget>[],
                  },

                  const SizedBox(height: 32),
                  // ESTE CAMPO PIDE LA CONTRASEÑA, NO UN PIN, y decía «PIN»
                  // con teclado NUMÉRICO. Las dos cosas estaban mal y la segunda
                  // era bloqueante:
                  //
                  // `intentarLoginOffline` verifica lo que se teclea contra
                  // `credencial.passwordHash`, que es el hash de la contraseña
                  // que la oficina le puso al vendedor —mínimo 12 caracteres, con
                  // letras—. No existe ningún PIN: no se genera en ninguna parte
                  // del sistema. Con el teclado numérico, esa contraseña NO SE
                  // PUEDE ESCRIBIR, así que el login diario era imposible y el
                  // único camino que funcionaba era volver a vincular el equipo
                  // con los tres datos, cada vez.
                  //
                  // Si algún día se quiere un PIN corto de verdad para el día a
                  // día, es otra cosa: hace falta que el servidor mande su hash
                  // aparte en `credencial_local`. Mientras no exista, la pantalla
                  // tiene que pedir lo que de verdad verifica.
                  TextField(
                    key: const Key('campo_pin'),
                    controller: _pin,
                    obscureText: true,
                    keyboardType: TextInputType.text,
                    autofocus: true,
                    textInputAction: TextInputAction.go,
                    onSubmitted: (_) => _verificando ? null : _entrar(),
                    decoration: const InputDecoration(
                      labelText: 'Tu contraseña',
                      helperText: 'La misma que te dio la oficina. No hay PIN aparte.',
                      border: OutlineInputBorder(),
                      prefixIcon: Icon(Icons.lock_outline),
                    ),
                  ),
                  const SizedBox(height: 16),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      key: const Key('boton_entrar'),
                      onPressed: _verificando ? null : _entrar,
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        child: _verificando
                            ? const SizedBox(
                                height: 20,
                                width: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('Entrar'),
                      ),
                    ),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 20),
                    _Aviso(motivo: _error!),
                  ],
                  const SizedBox(height: 28),
                  const Divider(),
                  if (!_mostrarGerencia)
                    TextButton.icon(
                      key: const Key('boton_abrir_gerencia'),
                      onPressed: _verificando
                          ? null
                          : () => setState(() => _mostrarGerencia = true),
                      icon: const Icon(Icons.insights_outlined),
                      label: const Text('Entrar como Gerencia'),
                    )
                  else ...[
                    const SizedBox(height: 8),
                    Text(
                      'Gerencia entra con señal: el tablero consulta al '
                      'servidor en vivo.',
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('campo_codigo_gerencia'),
                      controller: _codigo,
                      textCapitalization: TextCapitalization.characters,
                      decoration: const InputDecoration(
                        labelText: 'Código de usuario',
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.badge_outlined),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('campo_password_gerencia'),
                      controller: _password,
                      obscureText: true,
                      textInputAction: TextInputAction.go,
                      onSubmitted: (_) =>
                          _verificando ? null : _entrarComoGerencia(),
                      decoration: const InputDecoration(
                        labelText: 'Contraseña',
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.password_outlined),
                      ),
                    ),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        key: const Key('boton_entrar_gerencia'),
                        onPressed: _verificando ? null : _entrarComoGerencia,
                        child: const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('Entrar al tablero'),
                        ),
                      ),
                    ),
                    if (_errorEnLinea != null) ...[
                      const SizedBox(height: 16),
                      Container(
                        key: const Key('aviso_login_en_linea'),
                        width: double.infinity,
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.errorContainer,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          _errorEnLinea!,
                          style: TextStyle(
                            color:
                                Theme.of(context).colorScheme.onErrorContainer,
                          ),
                        ),
                      ),
                    ],
                  ],
                  // ──────────────────────────────────────────────────
                  // Vincular el equipo: el paso que faltaba
                  // ──────────────────────────────────────────────────
                  // Sin esto, un teléfono recién instalado no tenía NINGÚN
                  // camino: el login sin señal necesita una credencial que solo
                  // el modo demo escribía, y la demo no existe en release.
                  //
                  // Va al final y plegado porque se usa UNA VEZ en la vida del
                  // equipo. Lo de todos los días es el PIN de arriba.
                  const SizedBox(height: 32),
                  const Divider(),
                  const SizedBox(height: 8),
                  if (!_mostrarVinculo)
                    OutlinedButton.icon(
                      key: const Key('boton_abrir_vinculo'),
                      onPressed: _verificando
                          ? null
                          : () => setState(() => _mostrarVinculo = true),
                      icon: const Icon(Icons.phonelink_setup_outlined),
                      label: const Text('Vincular este equipo'),
                    )
                  else ...[
                    Text(
                      'Solo la primera vez, y con señal. La oficina vincula el '
                      'equipo en el panel y te da el identificador.',
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('campo_codigo_vinculo'),
                      controller: _codigo,
                      textCapitalization: TextCapitalization.characters,
                      decoration: const InputDecoration(
                        labelText: 'Tu código de vendedor',
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.badge_outlined),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('campo_password_vinculo'),
                      controller: _password,
                      obscureText: true,
                      decoration: const InputDecoration(
                        labelText: 'Tu contraseña',
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.password_outlined),
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('campo_equipo_vinculo'),
                      controller: _equipo,
                      textInputAction: TextInputAction.go,
                      textCapitalization: TextCapitalization.characters,
                      autocorrect: false,
                      onSubmitted: (_) => _verificando ? null : _vincular(),
                      decoration: const InputDecoration(
                        labelText: 'Clave del equipo',
                        hintText: 'RUTA4',
                        helperText: 'Te la da la oficina: panel → Teléfonos',
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.qr_code_2_outlined),
                      ),
                    ),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        key: const Key('boton_vincular'),
                        onPressed: _verificando ? null : _vincular,
                        child: const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('Vincular y entrar'),
                        ),
                      ),
                    ),
                    if (_errorVinculo != null) ...[
                      const SizedBox(height: 16),
                      Container(
                        key: const Key('aviso_vinculo'),
                        width: double.infinity,
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.errorContainer,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Text(
                          _errorVinculo!,
                          style: TextStyle(
                            color:
                                Theme.of(context).colorScheme.onErrorContainer,
                          ),
                        ),
                      ),
                    ],
                  ],
                  // Este bloque NO EXISTE en un build de release: las dos
                  // constantes que lo gobiernan son de compilación, así que el
                  // compilador lo elimina del árbol. Ver lib/src/demo.dart.
                  if (modoDemoDisponible) ...[
                    const SizedBox(height: 32),
                    const Divider(),
                    const SizedBox(height: 8),
                    Text(
                      'Compilado en modo demo',
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                    const SizedBox(height: 8),
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        key: const Key('boton_modo_demo'),
                        onPressed: _verificando ? null : _entrarEnModoDemo,
                        icon: const Icon(Icons.science_outlined),
                        label: const Text('Sembrar datos y entrar'),
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'PIN de demo: $pinDemo',
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                  ],
                  // Hasta abajo, la versión instalada: con el teléfono en la
                  // mano es como se sabe si el APK nuevo de verdad quedó.
                  if (ref.watch(versionDeLaAppProvider).valueOrNull
                      case final version?) ...[
                    const SizedBox(height: 32),
                    Text(
                      'Versión $version',
                      key: const Key('version_app'),
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// El mensaje importa: "PIN incorrecto" y "conéctate para renovar" piden cosas
/// distintas del vendedor, y confundirlos lo deja parado a media ruta sin saber
/// qué hacer.
class _Aviso extends StatelessWidget {
  const _Aviso({required this.motivo});

  final ResultadoLogin motivo;

  (IconData, String, String?) get _contenido => switch (motivo) {
        ResultadoLogin.passwordIncorrecta => (
            Icons.error_outline,
            'PIN incorrecto',
            null,
          ),
        ResultadoLogin.credencialVencida => (
            Icons.wifi_off_outlined,
            'Tu acceso venció',
            'Conéctate a internet una vez para renovarlo.',
          ),
        ResultadoLogin.sinCredencial => (
            Icons.cloud_off_outlined,
            'Este equipo no tiene sesión',
            'Necesitas conectarte a internet la primera vez.',
          ),
        ResultadoLogin.credencialCorrupta => (
            Icons.warning_amber_outlined,
            'La sesión guardada no sirve',
            'Conéctate a internet para volver a entrar.',
          ),
        ResultadoLogin.ok => (Icons.check, '', null),
      };

  @override
  Widget build(BuildContext context) {
    final (icono, titulo, detalle) = _contenido;
    final colores = Theme.of(context).colorScheme;
    return Container(
      key: const Key('aviso_login'),
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colores.errorContainer,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Icon(icono, color: colores.onErrorContainer),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(titulo,
                    style: TextStyle(
                      color: colores.onErrorContainer,
                      fontWeight: FontWeight.w600,
                    )),
                if (detalle != null)
                  Text(detalle, style: TextStyle(color: colores.onErrorContainer)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
