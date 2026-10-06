/// Catálogo de la visita: qué le puedo vender a este cliente, y a qué precio.
///
/// ─────────────────────────────────────────────────────────────────────────
/// CÓMO SE USA ESTA PANTALLA DE VERDAD
/// ─────────────────────────────────────────────────────────────────────────
/// De pie, con el cliente enfrente, a pleno sol, con una mano. El vendedor no
/// "navega": busca, toca +, y sigue. Por eso:
///
/// · El precio se ve **sin tocar nada**. Un precio a un toque de distancia es un
///   precio que el vendedor se aprende de memoria y luego se equivoca.
/// · El "+" está en el renglón, no adentro de un detalle.
/// · Lo que queda en el camión **baja mientras agrega**, porque es lo que
///   determina si puede prometer o no.
/// · El total vive abajo, fijo, siempre visible. Es el número que el cliente le
///   va a preguntar.
///
/// No hay campo de precio, y no por omisión: el vendedor no otorga descuentos
/// (ADR 0002 §7). El precio sale del catálogo y no hay dónde escribirlo.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../datos/repo_catalogo.dart';
import '../../estado/carrito.dart';
import 'carrito.dart';
import 'merma.dart';
import 'no_drop.dart';

class PantallaCatalogo extends ConsumerStatefulWidget {
  const PantallaCatalogo({super.key});

  @override
  ConsumerState<PantallaCatalogo> createState() => _EstadoCatalogo();
}

class _EstadoCatalogo extends ConsumerState<PantallaCatalogo> {
  final _busqueda = TextEditingController();

  @override
  void dispose() {
    _busqueda.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cliente = ref.watch(clienteDeLaVisitaProvider);
    final lista = ref.watch(listaDeLaVisitaProvider);
    final productos = ref.watch(catalogoProvider);
    final carrito = ref.watch(carritoProvider);
    final sinCarga = ref.watch(sinCargaActivaProvider);

    // Un rechazo se avisa y se olvida: el vendedor necesita saber cuánto sí
    // cabe, no quedarse con un cartel pegado en la pantalla.
    ref.listen(ultimoRechazoProvider, (_, rechazo) {
      if (rechazo == null) return;
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(_mensajeDeRechazo(rechazo))));
    });

    return Scaffold(
      appBar: AppBar(
        title: Text(cliente?.nombreComercial ?? 'Catálogo'),
        // Las dos salidas de la visita que NO son una venta. Viven aquí, dentro
        // de la visita, porque las dos necesitan al cliente: un no-drop sin
        // cliente no mide nada, y una devolución sin él no se puede revisar
        // contra su venta.
        //
        // En un menú y no como botones: lo que se busca en esta pantalla es
        // vender, y dos iconos compitiendo con el carrito harían más lento el
        // caso normal para acelerar el excepcional.
        actions: [
          if (cliente != null)
            PopupMenuButton<String>(
              key: const Key('menu_de_visita'),
              icon: const Icon(Icons.more_vert),
              onSelected: (opcion) {
                final destino = switch (opcion) {
                  'no_drop' => MaterialPageRoute<void>(
                      builder: (_) => PantallaNoDrop(cliente: cliente),
                    ),
                  'cambio' => MaterialPageRoute<void>(
                      builder: (_) => PantallaMerma(
                        cliente: cliente,
                        tipoInicial: TipoDeMerma.cambio,
                      ),
                    ),
                  _ => MaterialPageRoute<void>(
                      builder: (_) => PantallaMerma(cliente: cliente),
                    ),
                };
                Navigator.of(context).push(destino);
              },
              itemBuilder: (_) => const [
                PopupMenuItem(
                  value: 'no_drop',
                  child: ListTile(
                    leading: Icon(Icons.do_not_disturb_on_outlined),
                    title: Text('No me compró'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
                PopupMenuItem(
                  value: 'devolucion',
                  child: ListTile(
                    leading: Icon(Icons.undo),
                    title: Text('Me devolvió mercancía'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
                // Fresco por caducado o dañado, sin dinero (octubre 2026).
                PopupMenuItem(
                  value: 'cambio',
                  key: Key('opcion_cambio'),
                  child: ListTile(
                    leading: Icon(Icons.swap_horiz),
                    title: Text('Cambio físico'),
                    subtitle: Text('Caducado o dañado, sin cobro'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ],
            ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(64),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
            child: TextField(
              key: const Key('campo_busqueda_catalogo'),
              controller: _busqueda,
              onChanged: (v) =>
                  ref.read(busquedaCatalogoProvider.notifier).state = v,
              decoration: const InputDecoration(
                hintText: 'Buscar producto o código',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
                filled: true,
                isDense: true,
              ),
            ),
          ),
        ),
      ),
      bottomNavigationBar: _BarraTotal(carrito: carrito),
      body: Column(
        children: [
          if (lista == null)
            const _Aviso(
              clave: 'aviso_sin_lista',
              icono: Icons.price_change_outlined,
              titulo: 'No hay lista de precios',
              detalle: 'Sincroniza una vez para bajar el catálogo y sus precios.',
            )
          else if (lista.esPorOmision)
            const _Aviso(
              clave: 'aviso_lista_por_omision',
              icono: Icons.info_outline,
              titulo: 'Se cotiza con la lista general',
              detalle: 'Este cliente todavía no tiene tarifa asignada por la oficina.',
              severo: false,
            ),
          if (sinCarga)
            const _Aviso(
              clave: 'aviso_sin_carga',
              icono: Icons.local_shipping_outlined,
              titulo: 'El camión no trae carga',
              detalle: 'Sin carga del día no se puede vender. Avisa a la bodega.',
            ),
          Expanded(
            child: productos.isEmpty
                ? const Center(
                    key: Key('catalogo_vacio'),
                    child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text(
                        'No hay productos que coincidan',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  )
                : ListView.separated(
                    key: const Key('lista_catalogo'),
                    padding: const EdgeInsets.only(bottom: 16),
                    itemCount: productos.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (_, i) => _RenglonProducto(producto: productos[i]),
                  ),
          ),
        ],
      ),
    );
  }
}

String _mensajeDeRechazo(ResultadoCarrito rechazo) => switch (rechazo.motivo) {
      MotivoRechazo.sinExistencia => _sinExistencia(rechazo.desglose),
      MotivoRechazo.noVaEnLaCarga => 'Ese producto no va en la carga de hoy',
      MotivoRechazo.cantidadInvalida => 'Cantidad no válida',
      null => '',
    };

/// "Solo quedan 2 cajas y 6 piezas".
///
/// Probado en campo (POCO M5s, septiembre 2026): el decimal no se entiende.
/// "2.500 cajas" obliga al vendedor a traducirlo de cabeza; media caja no existe
/// en un camión. Lo que existe son 2 cajas y 6 piezas sueltas, que es exactamente
/// lo que le va a decir al cliente.
String _sinExistencia(DesgloseDisponible? d) {
  if (d == null || d.nadaCabe) return 'Ya no queda nada de ese producto en el camión';

  final partes = <String>[
    if (d.hayEnteras) _conUnidad(d.enteras, d.unidadCodigo),
    // Cuando la presentación ya es la unidad base, las "sueltas" serían la misma
    // unidad repetida: "40 piezas y 0 piezas".
    if (d.haySueltas && d.unidadCodigo != d.unidadBaseCodigo)
      _sueltas(d.sueltasEnBase, d.unidadBaseCodigo),
  ];
  return 'Solo quedan ${partes.join(' y ')} en el camión';
}

String _conUnidad(int cuantas, String codigo) {
  final (singular, plural) = _nombreDeUnidad(codigo);
  return '$cuantas ${cuantas == 1 ? singular : plural}';
}

String _sueltas(Cantidad cantidad, String codigo) {
  final (singular, plural) = _nombreDeUnidad(codigo);
  final texto = cantidad.textoCorto;
  return '$texto ${cantidad.milesimos == 1000 ? singular : plural}';
}

/// El nombre en español de las unidades que el negocio maneja.
///
/// `unidades_medida.nombre` vive en el servidor, pero el dispositivo no espeja
/// esa tabla: son cuatro códigos que no cambian, y sincronizar una tabla entera
/// para traducir dos palabras no se paga. Un código desconocido cae a sí mismo,
/// que es feo pero nunca miente.
(String, String) _nombreDeUnidad(String codigo) => switch (codigo) {
      'PZA' => ('pieza', 'piezas'),
      'CAJA' => ('caja', 'cajas'),
      'KG' => ('kilo', 'kilos'),
      'DISPLAY' => ('display', 'displays'),
      _ => (codigo, codigo),
    };

/// Un renglón del catálogo.
///
/// Muestra el precio de cada presentación y un "+" por presentación. Dos
/// presentaciones son dos formas de pedir lo mismo —"dame una caja" y "dame tres
/// piezas"—, y el vendedor las alterna sin pensar.
class _RenglonProducto extends ConsumerWidget {
  const _RenglonProducto({required this.producto});

  final ProductoEnCatalogo producto;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    final disponible = disponibleReal(ref, producto.id);
    final agotado = !producto.vaEnLaCarga || disponible.esCero;

    return Opacity(
      // Agotado se ve, no se esconde: el vendedor necesita saber que el producto
      // existe para pedirlo mañana.
      opacity: agotado ? 0.55 : 1,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        producto.nombre,
                        style: const TextStyle(
                          fontWeight: FontWeight.w600,
                          fontSize: 15,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        producto.sku,
                        style: TextStyle(
                          fontSize: 12,
                          color: colores.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
                _Existencia(
                  key: Key('existencia_${producto.id}'),
                  disponible: disponible,
                  vaEnLaCarga: producto.vaEnLaCarga,
                ),
              ],
            ),
            const SizedBox(height: 8),
            for (final p in producto.presentaciones)
              _RenglonPresentacion(presentacion: p, bloqueado: agotado),
          ],
        ),
      ),
    );
  }
}

class _Existencia extends StatelessWidget {
  const _Existencia({
    super.key,
    required this.disponible,
    required this.vaEnLaCarga,
  });

  final Cantidad disponible;
  final bool vaEnLaCarga;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;

    if (!vaEnLaCarga) {
      return Text(
        'No va en la carga',
        style: TextStyle(fontSize: 11, color: colores.error),
      );
    }
    if (disponible.esCero) {
      return Text(
        'Agotado',
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          color: colores.error,
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Text(
          disponible.textoCorto,
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
        ),
        Text(
          'en camión',
          style: TextStyle(fontSize: 10, color: colores.onSurfaceVariant),
        ),
      ],
    );
  }
}

/// Una forma de vender el producto: su precio y los botones para moverla.
class _RenglonPresentacion extends ConsumerWidget {
  const _RenglonPresentacion({
    required this.presentacion,
    required this.bloqueado,
  });

  final PresentacionVendible presentacion;
  final bool bloqueado;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colores = Theme.of(context).colorScheme;
    final enCarrito = ref.watch(carritoProvider).cantidadDe(presentacion.llave);
    final control = ref.read(carritoProvider.notifier);

    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        children: [
          // La unidad y su equivalencia: "CAJA · 24 pza" le dice al vendedor
          // cuánto sale del camión sin que tenga que recordarlo.
          SizedBox(
            width: 108,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  presentacion.unidadCodigo,
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                ),
                if (!presentacion.factor.esUno)
                  Text(
                    '${presentacion.factor.textoCorto} pza',
                    style: TextStyle(fontSize: 11, color: colores.onSurfaceVariant),
                  ),
              ],
            ),
          ),
          Expanded(
            child: Text(
              // Cuatro decimales cuando el precio los tiene: mostrar 12.33
              // donde el precio es 12.3333 haría creer que la caja de 24
              // cuesta 295.92 en vez de 296.00.
              '\$${presentacion.precio.textoCorto}',
              key: Key('precio_${presentacion.llave}'),
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
            ),
          ),
          if (enCarrito.esCero)
            FilledButton(
              key: Key('agregar_${presentacion.llave}'),
              onPressed: bloqueado
                  ? null
                  : () => control.agregar(presentacion, Cantidad.deEnteros(1)),
              style: FilledButton.styleFrom(
                minimumSize: const Size(56, 40),
                padding: EdgeInsets.zero,
              ),
              child: const Icon(Icons.add, size: 22),
            )
          else
            _Contador(presentacion: presentacion, cantidad: enCarrito),
        ],
      ),
    );
  }
}

/// El contador de una presentación ya en el carrito.
///
/// Botones grandes: se tocan de pie, con el pulgar, y un toque que falla obliga
/// a volver a mirar la pantalla en medio de una conversación.
class _Contador extends ConsumerWidget {
  const _Contador({required this.presentacion, required this.cantidad});

  final PresentacionVendible presentacion;
  final Cantidad cantidad;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final control = ref.read(carritoProvider.notifier);
    final colores = Theme.of(context).colorScheme;

    return Container(
      decoration: BoxDecoration(
        color: colores.primaryContainer,
        borderRadius: BorderRadius.circular(24),
      ),
      child: Row(
        children: [
          IconButton(
            key: Key('menos_${presentacion.llave}'),
            visualDensity: VisualDensity.compact,
            onPressed: () => control.fijar(
              presentacion,
              cantidad - Cantidad.deEnteros(1),
            ),
            icon: const Icon(Icons.remove, size: 20),
          ),
          SizedBox(
            width: 32,
            child: Text(
              cantidad.textoCorto,
              key: Key('cantidad_${presentacion.llave}'),
              textAlign: TextAlign.center,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
            ),
          ),
          IconButton(
            key: Key('mas_${presentacion.llave}'),
            visualDensity: VisualDensity.compact,
            onPressed: () => control.agregar(presentacion, Cantidad.deEnteros(1)),
            icon: const Icon(Icons.add, size: 20),
          ),
        ],
      ),
    );
  }
}

/// El total, fijo abajo. Es el número que el cliente pregunta.
class _BarraTotal extends StatelessWidget {
  const _BarraTotal({required this.carrito});

  final Carrito carrito;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;

    return SafeArea(
      child: Container(
        key: const Key('barra_total_catalogo'),
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: BoxDecoration(
          color: colores.surfaceContainerHighest,
          border: Border(top: BorderSide(color: colores.outlineVariant)),
        ),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    carrito.estaVacio
                        ? 'Sin productos'
                        : '${carrito.cuantasLineas} '
                            '${carrito.cuantasLineas == 1 ? "línea" : "líneas"}',
                    style: TextStyle(fontSize: 12, color: colores.onSurfaceVariant),
                  ),
                  Text(
                    '\$${carrito.total.texto}',
                    key: const Key('total_catalogo'),
                    style: const TextStyle(
                      fontWeight: FontWeight.w800,
                      fontSize: 22,
                    ),
                  ),
                ],
              ),
            ),
            FilledButton.icon(
              key: const Key('boton_ver_carrito'),
              onPressed: carrito.estaVacio
                  ? null
                  : () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => const PantallaCarrito(),
                        ),
                      ),
              icon: const Icon(Icons.shopping_cart_outlined),
              label: const Text('Ver pedido'),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Aviso extends StatelessWidget {
  const _Aviso({
    required this.clave,
    required this.icono,
    required this.titulo,
    required this.detalle,
    this.severo = true,
  });

  final String clave;
  final IconData icono;
  final String titulo;
  final String detalle;
  final bool severo;

  @override
  Widget build(BuildContext context) {
    final colores = Theme.of(context).colorScheme;
    final fondo = severo ? colores.errorContainer : colores.tertiaryContainer;
    final frente = severo ? colores.onErrorContainer : colores.onTertiaryContainer;

    return Container(
      key: Key(clave),
      width: double.infinity,
      color: fondo,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        children: [
          Icon(icono, color: frente, size: 20),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(titulo,
                    style: TextStyle(color: frente, fontWeight: FontWeight.w700)),
                Text(detalle, style: TextStyle(color: frente, fontSize: 12)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
