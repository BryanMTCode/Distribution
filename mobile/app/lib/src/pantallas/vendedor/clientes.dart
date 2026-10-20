/// Lista de clientes de la ruta.
///
/// Sale entera de SQLite: se ve igual con señal o sin ella. No hay estado
/// "cargando del servidor" que deje al vendedor esperando frente al cliente.
///
/// Agrupada por día (ADR 0002 §123): en «Todos», cada día con su encabezado y
/// sus tiendas en el orden del recorrido —el número de la hoja se repite cada
/// día, así que sin agrupar se mezclaban—; en «Hoy», las demás de la ruta
/// también por día.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_clientes.dart';
import '../../datos/servicio_ubicacion.dart';
import '../../demo.dart';
import '../../estado/alta.dart';
import '../../estado/carrito.dart';
import '../../estado/sesion.dart';
import '../../estado/sincronizacion.dart';
import '../../marca.dart';
import '../impresora.dart';
import 'alta_cliente.dart';
import 'camion.dart';
import 'catalogo.dart';
import 'guia.dart';
import 'mapa.dart';
import 'mi_dia.dart';
import 'merma.dart';
import 'perfil_cliente.dart';

class PantallaClientes extends ConsumerWidget {
  const PantallaClientes({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sesion = ref.watch(sesionProvider);
    final todos = ref.watch(clientesProvider);
    final soloHoy = ref.watch(verSoloHoyProvider);
    final buscando = ref.watch(busquedaClientesProvider).trim().isNotEmpty;

    // El plan de visita (migración 0041 del servidor). Sin plan en ningún
    // cliente, la pantalla es la de siempre: no hay «hoy» que mostrar. Y al
    // buscar se busca en TODOS: el cliente que llama por teléfono pidiendo
    // mercancía no tiene por qué tocar hoy.
    final hayPlan = todos.any(
      (c) => c.plan.isNotEmpty || c.reprogramadaHoy || c.proximaReprogramada != null,
    );
    final deHoy = todos.where((c) => c.tocaHoy).toList();
    final filtrando = hayPlan && soloHoy && !buscando;
    final visitadosHoy = deHoy.where((c) => c.visitadoHoy).length;
    // En «Hoy», primero las que tocan y abajo las demás de la ruta: el vendedor
    // también pasa a dejarle algo a una tienda que hoy no le toca (ADR 0002
    // §108). «Todos» es la ruta entera en su orden.
    final otras = filtrando ? todos.where((c) => !c.tocaHoy).toList() : const <ClienteEnRuta>[];
    final otrasPlegadas = ref.watch(otrasTiendasPlegadasProvider);
    // «Todos», con plan y sin buscar: por día (ADR 0002 §123).
    final porDias = hayPlan && !soloHoy && !buscando;
    final plegados = ref.watch(diasPlegadosProvider);
    final hoy = ref.watch(relojProvider)().toLocal().weekday % 7;
    // «Cerca de mí» (ADR 0002 §129): las de la vista, de la más cercana a la más
    // lejana, sin días ni «Las demás»; las que no tienen ubicación, al final.
    final cerca = ref.watch(cercaDeMiProvider);
    final distancias = <String, double>{
      if (cerca != null)
        for (final c in todos)
          if (c.conUbicacion)
            c.id: cerca.distanciaA(
              Ubicacion(lat: c.lat!, lng: c.lng!, origen: OrigenUbicacion.gps),
            ),
    };
    final porDistancia = cerca == null
        ? null
        : ([...(filtrando ? deHoy : todos)]
          ..sort((a, b) => (distancias[a.id] ?? double.infinity)
              .compareTo(distancias[b.id] ?? double.infinity)));
    final renglones = porDistancia != null
        ? <Object>[...porDistancia]
        : <Object>[
            if (filtrando && deHoy.isEmpty) const _SinNadieHoy(),
            if (!porDias) ...(filtrando ? deHoy : todos),
            if (otras.isNotEmpty) _EncabezadoDeOtras(otras.length, plegadas: otrasPlegadas),
            // Las demás, también por día; sin plegarse cada uno: ya se pliegan juntas.
            if (otras.isNotEmpty && !otrasPlegadas)
              for (final (dia, suyas) in _porDia(otras)) ...[
                _EncabezadoDeDia(dia, suyas.length, clave: 'dia_otras', esHoy: false),
                ...suyas,
              ],
            if (porDias)
              for (final (dia, suyas) in _porDia(todos)) ...[
                _EncabezadoDeDia(
                  dia,
                  suyas.length,
                  clave: 'dia_ruta',
                  esHoy: dia == hoy,
                  plegado: plegados.contains(_claveDeDia(dia)),
                  alTocar: () {
                    final ahora = {...ref.read(diasPlegadosProvider)};
                    if (!ahora.remove(_claveDeDia(dia))) ahora.add(_claveDeDia(dia));
                    ref.read(diasPlegadosProvider.notifier).state = ahora;
                  },
                ),
                if (!plegados.contains(_claveDeDia(dia))) ...suyas,
              ],
          ];

    final cola = ref.watch(resumenColaProvider);
    final sync = ref.watch(syncProvider);

    ref.listen(syncProvider, (_, actual) {
      final mensaje = _mensajeDeSync(actual);
      if (mensaje == null) return;
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(mensaje)));
    });

    return Scaffold(
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('boton_nuevo_cliente'),
        onPressed: () => Navigator.of(context).push(
          MaterialPageRoute<void>(builder: (_) => const PantallaAltaCliente()),
        ),
        icon: const Icon(Icons.add_business_outlined),
        label: const Text('Nuevo'),
      ),
      appBar: AppBar(
        // La marca de demo va en un widget aparte, no dentro del título: si
        // cambiara el texto del título, cualquier prueba que lo busque se
        // rompería solo por compilar en modo demo. Nadie debe confundir datos
        // sembrados con la ruta real, pero eso no justifica acoplar las dos
        // cosas.
        title: Row(
          // `Flexible` con elipsis, y no un `Text` a secas: el AppBar le da al
          // título el espacio que sobra después de las acciones, y con tres
          // iconos más la marca DEMO ese espacio no alcanza. Sin esto la fila
          // desbordaba 46 píxeles —lo encontró la suite en modo demo— y cada
          // pantalla nueva en la barra volvería a romperla. Que el título cede
          // es lo correcto: el nombre de la pantalla se adivina, un botón
          // recortado no se puede tocar.
          children: [
            // La ruta que trae, «Ruta 1101» (ADR 0002 §143). De la credencial
            // guardada, que se pone al día al renovar el acceso; sin rutas, «Mi ruta».
            Flexible(
              child: Text(
                (ref.watch(credencialGuardadaProvider).valueOrNull ??
                            (sesion is SesionAbierta ? sesion.credencial : null))
                        ?.tituloDeLaRuta ??
                    'Mi ruta',
                key: const Key('titulo_ruta'),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (modoDemoDisponible) ...[
              const SizedBox(width: 8),
              Container(
                key: const Key('marca_demo'),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.error,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  'DEMO',
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: Theme.of(context).colorScheme.onError,
                  ),
                ),
              ),
            ],
          ],
        ),
        actions: [
          IconButton(
            key: const Key('boton_sincronizar'),
            tooltip: 'Sincronizar',
            icon: sync is SyncEnCurso
                ? const SizedBox(
                    height: 18,
                    width: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.sync),
            onPressed: sync is SyncEnCurso
                ? null
                : () => ref.read(syncProvider.notifier).sincronizar(),
          ),
          // La merma del camión no pertenece a ninguna visita: la caja se
          // revienta entre tienda y tienda. Por eso vive aquí y no dentro del
          // catálogo de un cliente.
          // «Mi día» va PRIMERO entre las acciones, antes de merma y de salir:
          // es lo que el vendedor abre varias veces al día, y las otras dos son
          // excepcionales. El orden de los iconos es el orden de uso.
          IconButton(
            key: const Key('boton_camion'),
            tooltip: 'Mi camión',
            icon: const Icon(Icons.local_shipping_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const PantallaCamion()),
            ),
          ),
          IconButton(
            key: const Key('boton_mi_dia'),
            tooltip: 'Mi día',
            icon: const Icon(Icons.payments_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const PantallaMiDia()),
            ),
          ),
          // Merma y salir van a un menú, no a la barra: con tres iconos más la
          // marca DEMO la fila DESBORDABA 46 píxeles —lo encontró la suite en modo
          // demo, y en un teléfono angosto habría pasado igual sin modo demo—.
          //
          // El criterio para elegir qué se queda a la vista es la frecuencia: «Mi
          // día» se abre varias veces en una ruta; una merma es excepcional y salir
          // es una vez al día. Un icono visible que desborda es peor que uno a dos
          // toques.
          PopupMenuButton<String>(
            key: const Key('menu_vendedor'),
            tooltip: 'Más',
            onSelected: (opcion) {
              if (opcion == 'merma') {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const PantallaMerma()),
                );
              } else if (opcion == 'impresora') {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const PantallaImpresora()),
                );
              } else if (opcion == 'salir') {
                ref.read(sesionProvider.notifier).salir();
              }
            },
            itemBuilder: (_) => const [
              PopupMenuItem(
                value: 'merma',
                key: Key('boton_merma'),
                child: ListTile(
                  leading: Icon(Icons.delete_outline),
                  title: Text('Registrar merma'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              // La impresora del camión: elegirla y probarla (ADR 0002 §139).
              PopupMenuItem(
                value: 'impresora',
                key: Key('boton_impresora'),
                child: ListTile(
                  leading: Icon(Icons.print_outlined),
                  title: Text('Impresora'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: 'salir',
                key: Key('boton_salir'),
                child: ListTile(
                  leading: Icon(Icons.logout),
                  title: Text('Salir'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ],
          ),
        ],
        bottom: cola.todoSincronizado ? null : _BarraPendientes(cola: cola),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              key: const Key('campo_busqueda'),
              onChanged: (v) =>
                  ref.read(busquedaClientesProvider.notifier).state = v,
              decoration: const InputDecoration(
                hintText: 'Buscar por nombre, código o teléfono',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ),
          // Siempre, aunque nadie tenga plan ni ubicación: el «Mapa» sirve para
          // bajar las calles con WiFi antes de salir (ADR 0002 §130).
          if (todos.isNotEmpty || hayPlan)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
              // `Wrap` y no `Row`: en 360 de ancho «Hoy · Todos», «Cerca de mí», el
              // mapa y el avance no caben en un renglón, y lo que no cabe baja.
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  if (hayPlan)
                    SegmentedButton<bool>(
                      key: const Key('filtro_hoy'),
                      showSelectedIcon: false,
                      segments: [
                        ButtonSegment(
                          value: true,
                          label: Text('Hoy · ${deHoy.length}'),
                        ),
                        const ButtonSegment(value: false, label: Text('Todos')),
                      ],
                      selected: {soloHoy},
                      onSelectionChanged: (s) =>
                          ref.read(verSoloHoyProvider.notifier).state = s.first,
                    ),
                  if (todos.any((c) => c.conUbicacion)) _CercaDeMi(activo: cerca != null),
                  ActionChip(
                    key: const Key('abrir_mapa'),
                    avatar: const Icon(Icons.map_outlined, size: 18),
                    label: const Text('Mapa'),
                    onPressed: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(builder: (_) => const PantallaMapa()),
                    ),
                  ),
                  if (deHoy.isNotEmpty)
                    Text(
                      key: const Key('avance_de_hoy'),
                      'Visitados $visitadosHoy de ${deHoy.length}',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                ],
              ),
            ),
          Expanded(
            child: renglones.isEmpty
                ? const Center(
                    key: Key('lista_vacia'),
                    child: Text('No hay clientes que coincidan'),
                  )
                : ListView.separated(
                    key: const Key('lista_clientes'),
                    itemCount: renglones.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (_, i) => switch (renglones[i]) {
                      final _SinNadieHoy aviso => aviso,
                      final _EncabezadoDeDia encabezado => encabezado,
                      _EncabezadoDeOtras(:final cuantas, :final plegadas) => _EncabezadoDeOtras(
                          cuantas,
                          plegadas: plegadas,
                          alTocar: () => ref
                              .read(otrasTiendasPlegadasProvider.notifier)
                              .state = !plegadas,
                        ),
                      final ClienteEnRuta cliente => _Renglon(
                          cliente: cliente,
                          distanciaMetros: distancias[cliente.id],
                          // Tocar un cliente ABRE SU VISITA: fija de quién es el
                          // carrito y entra al catálogo con SUS precios. El
                          // catálogo no existe "en general" — el precio depende
                          // de su lista.
                          alTocar: () {
                            ref.read(clienteEnVisitaProvider.notifier).state = cliente.id;
                            ref.read(busquedaCatalogoProvider.notifier).state = '';
                            Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) => const PantallaCatalogo(),
                              ),
                            );
                          },
                        ),
                      _ => const SizedBox.shrink(),
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

/// Lo que se le dice al vendedor después de sincronizar.
///
/// Cada final pide algo distinto de él: "sin señal" es esperar, "vuelve a
/// entrar" es actuar, y "quedaron con error" es avisar a la oficina. Un mensaje
/// genérico lo dejaría sin saber qué hacer.
String? _mensajeDeSync(EstadoSync estado) => switch (estado) {
      SyncInactiva() || SyncEnCurso() => null,
      SyncSinSesionEnLinea() =>
        'Entraste sin señal. Conéctate para enviar lo pendiente.',
      // Los dos estados del borrado remoto (Fase 9) no se resuelven con un
      // aviso: tienen pantalla propia, porque no hay nada más que hacer en esta.
      // Ver `PortalPorRol`.
      SyncEquipoBorrado() || SyncBorradoPendiente() => null,
      SyncTerminada(:final resultado) => switch (resultado.fin) {
          FinDeSync.sinRed => 'Sin señal. Nada se perdió, se reintenta luego.',
          FinDeSync.sesionInvalida => 'Tu sesión venció. Vuelve a entrar.',
          FinDeSync.servidorCaido => 'El servidor no responde. Se reintenta luego.',
          FinDeSync.parcial => 'Se envió una parte. Falta cola por subir.',
          // Los dos de borrado no llegan aquí: `ControladorSync` los convierte
          // en `SyncEquipoBorrado` / `SyncBorradoPendiente` antes de publicar
          // el estado. Se enumeran porque el switch es exhaustivo, y así un
          // valor nuevo del enum rompe la compilación en vez de caer en un
          // `default` silencioso.
          FinDeSync.borradoListo || FinDeSync.borradoPendiente => null,
          FinDeSync.completa => resultado.huboActividad
              ? _resumenCompleto(resultado)
              : 'Todo al día.',
        },
    };

String _resumenCompleto(ResultadoSincronizacion r) {
  final partes = <String>[
    if (r.sobresConfirmados > 0) 'enviadas ${r.sobresConfirmados}',
    if (r.deltasAplicados > 0) 'actualizados ${r.deltasAplicados}',
    if (r.sobresEnCuarentena > 0) '${r.sobresEnCuarentena} con error',
  ];
  return 'Listo: ${partes.join(' · ')}';
}

/// Mientras haya cola sin sincronizar, se ve. Un número de pendientes que crece
/// es la primera señal de que algo va mal, y el vendedor es quien lo nota
/// primero.
class _BarraPendientes extends ConsumerWidget implements PreferredSizeWidget {
  const _BarraPendientes({required this.cola});

  final ResumenCola cola;

  // 32 px alcanzan para una línea de texto y no para un botón dentro de la fila:
  // el primer intento puso un TextButton ahí y el toque NO LO ALCANZABA —la fila
  // se recortaba dentro de la barra—. Lo detectó la prueba de widget porque el
  // toque no hacía nada; en un teléfono habría sido un botón a la vista que no
  // responde, que es peor.
  //
  // Así que no hay botón: cuando hay algo con error, LA BARRA COMPLETA es lo que
  // se toca. Un solo widget, del ancho de la pantalla, imposible de recortar y
  // más fácil de acertar con el pulgar en un camión.
  @override
  Size get preferredSize => const Size.fromHeight(32);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    final hayErrores = cola.enCuarentena > 0;
    final cuarentena = hayErrores ? ' · ${cola.enCuarentena} con error' : '';

    final contenido = Container(
      width: double.infinity,
      color: hayErrores ? colores.errorContainer : colores.tertiaryContainer,
      padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
      child: Row(
        children: [
          Icon(
            hayErrores ? Icons.refresh : Icons.cloud_upload_outlined,
            size: 16,
            color: hayErrores ? colores.onErrorContainer : colores.onTertiaryContainer,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              hayErrores
                  ? 'Por enviar: ${cola.pendientes}$cuarentena · toca para reintentar'
                  : 'Por enviar: ${cola.pendientes}',
              style: TextStyle(
                color:
                    hayErrores ? colores.onErrorContainer : colores.onTertiaryContainer,
                fontSize: 13,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );

    if (!hayErrores) {
      return Container(key: const Key('barra_pendientes'), child: contenido);
    }
    return InkWell(
      key: const Key('barra_pendientes'),
      onTap: () => ref.read(syncProvider.notifier).reintentarLoQueFallo(),
      child: contenido,
    );
  }
}

/// «Hoy» sin nadie del plan: se dice, y abajo siguen las demás de la ruta.
class _SinNadieHoy extends StatelessWidget {
  const _SinNadieHoy();

  @override
  Widget build(BuildContext context) => const Padding(
        key: Key('lista_vacia'),
        padding: EdgeInsets.all(16),
        child: Text('Hoy no te toca nadie según el plan. Abajo están las demás de tu ruta.'),
      );
}

/// Los días como se trabajan, con el número de la base (`extract(dow)`, 0
/// domingo); al final, las que no tienen día.
const _ordenDeDias = [1, 2, 3, 4, 5, 6, 0];
const _nombreDeDia = {
  1: 'Lunes', 2: 'Martes', 3: 'Miércoles', 4: 'Jueves', 5: 'Viernes', 6: 'Sábado',
  0: 'Domingo',
};

String _claveDeDia(int? dia) => dia == null ? 'sin' : '$dia';

/// Las tiendas por día de su plan, en el orden en que llegan (el del
/// recorrido). La que toca dos días sale en los dos.
List<(int?, List<ClienteEnRuta>)> _porDia(List<ClienteEnRuta> clientes) {
  final grupos = <int?, List<ClienteEnRuta>>{};
  for (final c in clientes) {
    final dias = <int?>{for (final d in c.plan) d.dia};
    for (final d in dias.isEmpty ? const <int?>{null} : dias) {
      grupos.putIfAbsent(d, () => []).add(c);
    }
  }
  return [
    for (final d in [..._ordenDeDias, null])
      if (grupos[d] != null) (d, grupos[d]!),
  ];
}

/// El encabezado de un día: «Lunes · 45». En «Todos» se toca para plegarlo.
class _EncabezadoDeDia extends StatelessWidget {
  const _EncabezadoDeDia(
    this.dia,
    this.cuantas, {
    required this.clave,
    required this.esHoy,
    this.plegado = false,
    this.alTocar,
  });

  final int? dia;
  final int cuantas;
  final String clave;
  final bool esHoy;
  final bool plegado;
  final VoidCallback? alTocar;

  @override
  Widget build(BuildContext context) {
    final estilo = Theme.of(context).textTheme;
    return Material(
      key: Key('${clave}_${_claveDeDia(dia)}'),
      color: alTocar == null
          ? Theme.of(context).colorScheme.surface
          : Theme.of(context).colorScheme.surfaceContainerHighest,
      child: InkWell(
        onTap: alTocar,
        child: Padding(
          padding: EdgeInsets.fromLTRB(alTocar == null ? 16 : 12, 8, 12, 8),
          child: Row(
            children: [
              if (alTocar != null) ...[
                Icon(plegado ? Icons.chevron_right : Icons.expand_more),
                const SizedBox(width: 4),
              ],
              Expanded(
                child: Text(
                  '${dia == null ? 'Sin día de visita' : _nombreDeDia[dia]} · $cuantas',
                  style: alTocar == null
                      ? estilo.titleSmall
                      : const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
              if (esHoy)
                Text(
                  'hoy',
                  style: estilo.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.primary,
                    fontWeight: FontWeight.w700,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// El encabezado de las tiendas que hoy no tocan: se toca para plegarlas.
class _EncabezadoDeOtras extends StatelessWidget {
  const _EncabezadoDeOtras(this.cuantas, {required this.plegadas, this.alTocar});

  final int cuantas;
  final bool plegadas;
  final VoidCallback? alTocar;

  @override
  Widget build(BuildContext context) => Material(
        key: const Key('otras_de_la_ruta'),
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        child: InkWell(
          onTap: alTocar,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            child: Row(
              children: [
                Icon(plegadas ? Icons.chevron_right : Icons.expand_more),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    'Las demás de tu ruta · $cuantas',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text('hoy no te tocan', style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
        ),
      );
}

class _Renglon extends StatelessWidget {
  const _Renglon({
    required this.cliente,
    required this.alTocar,
    this.distanciaMetros,
  });

  final ClienteEnRuta cliente;
  final VoidCallback alTocar;

  /// Con «Cerca de mí», a cuánto está en línea recta (ADR 0002 §129).
  final double? distanciaMetros;

  @override
  Widget build(BuildContext context) {
    final subtitulo = [
      if (distanciaMetros != null) 'a ${distanciaEnPalabras(distanciaMetros!)}',
      if (cliente.codigo != null) cliente.codigo,
      if (cliente.direccion != null) cliente.direccion,
    ].whereType<String>().join(' · ');

    return ListTile(
      key: Key('cliente_${cliente.id}'),
      // Visitado hoy: la palomita en lugar del número de orden. Es lo que el
      // vendedor busca con la vista al recorrer la lista a media ruta. La que ya
      // le compró va en verde y la visita sin venta en gris; las que faltan
      // siguen en el rojo de la marca, con su número (ADR 0002 §153).
      leading: cliente.visitadoHoy
          ? CircleAvatar(
              key: Key('visitado_${cliente.id}'),
              backgroundColor: cliente.vendidoHoy ? colorDeVendida : colorDeSinVenta,
              foregroundColor: cliente.vendidoHoy ? Colors.white : letraDeSinVenta,
              child: Icon(
                cliente.vendidoHoy ? Icons.check : Icons.close,
                key: Key(cliente.vendidoHoy ? 'vendida_${cliente.id}' : 'sin_venta_${cliente.id}'),
                size: 20,
              ),
            )
          : CircleAvatar(
              child: Text(
                cliente.secuencia?.toString() ?? '—',
                style: const TextStyle(fontSize: 13),
              ),
            ),
      title: Row(
        children: [
          Expanded(child: Text(cliente.nombreComercial)),
          if (cliente.esLocal)
            const Padding(
              padding: EdgeInsets.only(left: 6),
              child: Icon(Icons.cloud_upload_outlined, size: 16),
            ),
        ],
      ),
      subtitle: _subtitulo(context, subtitulo),
      // La ubicación a un toque, sin entrar a la visita: con el GPS o escrita a
      // mano (ADR 0002 §84 y §107). En rojo la tienda que todavía no tiene: sin
      // ella la venta no tiene geocerca.
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Cómo llegar, sin mapa ni señal (ADR 0002 §129): solo la que ya tiene
          // dónde llegar.
          if (cliente.conUbicacion)
            IconButton(
              key: Key('guia_${cliente.id}'),
              tooltip: 'Cómo llegar',
              icon: const Icon(Icons.near_me_outlined),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => PantallaGuia(cliente: cliente),
                ),
              ),
            ),
          IconButton(
            key: Key('ubicar_${cliente.id}'),
            tooltip: cliente.conUbicacion ? 'Ubicación' : 'Ponerle ubicación',
            icon: Icon(
              cliente.conUbicacion
                  ? Icons.location_on_outlined
                  : Icons.add_location_alt_outlined,
              color: cliente.conUbicacion ? null : Theme.of(context).colorScheme.error,
            ),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => PantallaPerfilDelCliente(cliente: cliente),
              ),
            ),
          ),
        ],
      ),
      onTap: alTocar,
    );
  }

  /// Su código y dirección, y si la visita se movió («ven mañana», ADR 0002
  /// §103): hoy le toca porque lo pidió, o cuándo hay que volver.
  Widget? _subtitulo(BuildContext context, String datos) {
    final proxima = cliente.proximaReprogramada;
    final aviso = cliente.reprogramadaHoy
        ? 'Te pidió que volvieras hoy'
        : proxima != null
            ? 'Volver el ${diaEnPalabras(proxima)}'
            : null;
    if (aviso == null) return datos.isEmpty ? null : Text(datos);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (datos.isNotEmpty) Text(datos),
        Text(
          aviso,
          key: Key('reprogramada_${cliente.id}'),
          style: TextStyle(
            color: Theme.of(context).colorScheme.primary,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}

/// «Cerca de mí» (ADR 0002 §129): una lectura del GPS —sin datos— y la lista
/// queda de la más cercana a la más lejana. Otro toque la regresa a su orden.
class _CercaDeMi extends ConsumerStatefulWidget {
  const _CercaDeMi({required this.activo});

  final bool activo;

  @override
  ConsumerState<_CercaDeMi> createState() => _EstadoCercaDeMi();
}

class _EstadoCercaDeMi extends ConsumerState<_CercaDeMi> {
  bool _leyendo = false;

  Future<void> _alternar() async {
    if (widget.activo) {
      ref.read(cercaDeMiProvider.notifier).state = null;
      return;
    }
    setState(() => _leyendo = true);
    final lectura = await ref.read(servicioUbicacionProvider).leer();
    if (!mounted) return;
    setState(() => _leyendo = false);
    final motivo = switch (lectura) {
      GpsObtenido(:final ubicacion) => () {
          ref.read(cercaDeMiProvider.notifier).state = ubicacion;
          return null;
        }(),
      GpsApagado() => 'La ubicación del teléfono está apagada: préndela.',
      GpsSinPermiso() => 'La app no tiene permiso de ubicación.',
      GpsSinLectura() =>
        'El GPS no encontró los satélites. Sal de techo y vuelve a tocar.',
    };
    if (motivo != null) {
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(motivo)));
    }
  }

  @override
  Widget build(BuildContext context) => FilterChip(
        key: const Key('cerca_de_mi'),
        label: Text(_leyendo ? 'Buscando…' : 'Cerca de mí'),
        avatar: _leyendo
            ? const SizedBox(
                width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.near_me_outlined, size: 18),
        showCheckmark: false,
        selected: widget.activo,
        onSelected: _leyendo ? null : (_) => _alternar(),
      );
}
