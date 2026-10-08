/// Estado de la visita: el cliente que se está atendiendo y su carrito.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL CARRITO SOBREVIVE A QUE EL SISTEMA MATE LA APP
/// ─────────────────────────────────────────────────────────────────────────
/// Cada cambio se guarda en `carrito_borrador` (ver `dsd_core/borrador.dart`).
/// No es un lujo: los vendedores usan Android de gama baja, y veinte minutos en
/// un mercado con la app en segundo plano alcanzan para que el sistema la mate.
/// Sin borrador, el vendedor vuelve a una pantalla en blanco y tiene que rearmar
/// quince renglones frente al cliente — en la práctica no los rearma: los apunta
/// en papel, y deja de usar la app.
library;

import 'dart:math';

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../datos/impresora.dart';
import '../datos/repo_catalogo.dart';
import '../datos/repo_clientes.dart';
import 'sesion.dart';

final repoCatalogoProvider = Provider<RepoCatalogo>(
  (ref) => RepoCatalogo(ref.watch(baseLocalProvider).db),
);

/// El cliente que se está atendiendo ahora. `null` = nadie.
///
/// Abrir el catálogo siempre es "para un cliente": el precio depende de su
/// lista, y el crédito de su saldo. Un catálogo sin cliente mostraría precios
/// que no son de nadie.
final clienteEnVisitaProvider = StateProvider<String?>((_) => null);

final busquedaCatalogoProvider = StateProvider<String>((_) => '');

/// El cliente de la visita, ya con su crédito compuesto desde la cola local.
final clienteDeLaVisitaProvider = Provider<ClienteEnRuta?>((ref) {
  final id = ref.watch(clienteEnVisitaProvider);
  if (id == null) return null;
  // Se reconstruye cuando cambia la cola: una venta a crédito encolada baja el
  // disponible, y el vendedor tiene que verlo en la misma visita.
  ref.watch(resumenColaProvider);
  return ref.watch(repoClientesProvider).porId(id);
});

/// Con qué lista se le cotiza al cliente de la visita.
final listaDeLaVisitaProvider = Provider<ListaResuelta?>((ref) {
  final id = ref.watch(clienteEnVisitaProvider);
  if (id == null) return null;
  return ref.watch(repoCatalogoProvider).listaParaCliente(id);
});

/// Lo que trae el camión hoy. Se lee entero: el carrito lo consulta en cada
/// toque de "+", y una consulta por toque se sentiría pegajosa.
final existenciasProvider = Provider<ExistenciasCamion>((ref) {
  ref.watch(revisionDelCamionProvider);
  return ref.watch(repoCatalogoProvider).existencias();
});

final repoBorradorProvider = Provider<RepoBorrador>(
  (ref) => RepoBorrador(ref.watch(baseLocalProvider).db),
);

final repoFoliosProvider = Provider<RepoFolios>(
  (ref) => RepoFolios(ref.watch(baseLocalProvider).db),
);

/// Genera los identificadores de los documentos.
///
/// UUID v4 por ahora: el v7 del servidor ordena por tiempo, que sirve para los
/// índices de PostgreSQL, pero el dispositivo no depende de ese orden. Se
/// inyecta para que las pruebas produzcan ids predecibles.
final nuevoUuidProvider = Provider<String Function()>((_) => _uuidV4);

String _uuidV4() {
  final azar = Random.secure();
  final bytes = List<int>.generate(16, (_) => azar.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

/// `true` cuando el camión no trae carga. No es un error de la app, y se dice
/// con esas palabras.
final sinCargaActivaProvider = Provider<bool>((ref) {
  ref.watch(revisionDelCamionProvider);
  return ref.watch(repoCatalogoProvider).sinCargaActiva;
});

/// El catálogo cotizado para el cliente de la visita, filtrado por la búsqueda.
final catalogoProvider = Provider<List<ProductoEnCatalogo>>((ref) {
  // El catálogo dice cuánto queda de cada producto: ofrecerle al siguiente
  // cliente lo que ya se vendió es como se llega a una venta imposible.
  ref.watch(revisionDelCamionProvider);
  final lista = ref.watch(listaDeLaVisitaProvider);
  if (lista == null) return const [];
  return ref.watch(repoCatalogoProvider).paraCliente(
        listaPreciosId: lista.id,
        busqueda: ref.watch(busquedaCatalogoProvider),
      );
});

/// Lo último que pasó al tocar el carrito. La pantalla lo escucha para avisar
/// "solo quedan 4 cajas" sin que el carrito sepa nada de interfaz.
final ultimoRechazoProvider = StateProvider<ResultadoCarrito?>((_) => null);

class ControladorCarrito extends Notifier<Carrito> {
  @override
  Carrito build() {
    // Cambiar de cliente empieza un carrito nuevo. Arrastrar líneas de una
    // tienda a la siguiente sería la forma más rápida de facturarle a quien no
    // pidió nada.
    final clienteId = ref.watch(clienteEnVisitaProvider);
    if (clienteId == null) return const Carrito();

    // Si la app murió a media visita, el borrador vuelve tal cual estaba —con
    // los mismos precios que se le cotizaron al cliente—.
    final borrador = ref.read(repoBorradorProvider).leer();
    if (borrador != null && borrador.clienteId == clienteId) {
      return borrador.carrito;
    }
    return const Carrito();
  }

  /// Cada cambio se persiste. Es una escritura chica y local; el costo de
  /// hacerla en cada toque es imperceptible al lado de perder el pedido.
  void _persistir() {
    final clienteId = ref.read(clienteEnVisitaProvider);
    if (clienteId == null) return;
    ref.read(repoBorradorProvider).guardar(
          clienteId,
          state,
          ahora: ref.read(relojProvider)().toUtc().toIso8601String(),
        );
  }

  /// Agrega (o suma) una presentación.
  ///
  /// No recibe precio. No lo recibe porque no existe: el precio viene dentro de
  /// la presentación, resuelto del catálogo (ADR 0002 §7).
  ResultadoCarrito agregar(PresentacionVendible presentacion, Cantidad cantidad) =>
      _aplicar(
        state.agregar(
          presentacion,
          cantidad,
          existencias: ref.read(existenciasProvider),
        ),
      );

  ResultadoCarrito fijar(PresentacionVendible presentacion, Cantidad cantidad) =>
      _aplicar(
        state.fijarCantidad(
          presentacion,
          cantidad,
          existencias: ref.read(existenciasProvider),
        ),
      );

  void quitar(String llave) {
    state = state.quitar(llave);
    _persistir();
  }

  void vaciar() {
    state = state.vaciar();
    ref.read(repoBorradorProvider).limpiar();
  }

  /// Efectivo o transferencia: todo se paga en el acto (ADR 0002 §81).
  void cambiarFormaDePago(FormaDePago forma, {String? referencia}) {
    state = state.conFormaDePago(forma, referencia: referencia);
    _persistir();
  }

  /// Un rechazo **no** modifica el carrito: se publica para que la pantalla lo
  /// cuente y el estado se queda como estaba.
  ResultadoCarrito _aplicar(ResultadoCarrito resultado) {
    if (resultado.aceptado) {
      state = resultado.carrito;
      _persistir();
      ref.read(ultimoRechazoProvider.notifier).state = null;
    } else {
      ref.read(ultimoRechazoProvider.notifier).state = resultado;
    }
    return resultado;
  }
}

final carritoProvider = NotifierProvider<ControladorCarrito, Carrito>(
  ControladorCarrito.new,
);

/// Cuánto queda **disponible de verdad** de un producto: lo que trae el camión
/// menos lo que ya está en el carrito.
///
/// Es el número que se muestra en el catálogo, y baja mientras el vendedor
/// agrega. Mostrar la existencia sin descontar el carrito lo dejaría prometiendo
/// mercancía que él mismo ya comprometió.
Cantidad disponibleReal(WidgetRef ref, String productoId) {
  final existencias = ref.watch(existenciasProvider);
  final enCarrito = ref.watch(carritoProvider).comprometidoDe(productoId);
  final libre = existencias.disponible(productoId) - enCarrito;
  return libre.milesimos < 0 ? Cantidad.cero : libre;
}

// ---------------------------------------------------------------------------
// El cierre de la venta
// ---------------------------------------------------------------------------

/// Quién vende, desde dónde y con qué equipo.
///
/// Sale de la credencial guardada, no de la pantalla: el vendedor no elige a
/// nombre de quién factura.
final identidadDeVentaProvider = Provider<IdentidadDeVenta?>((ref) {
  final sesion = ref.watch(sesionProvider);
  if (sesion is! SesionAbierta) return null;
  final c = sesion.credencial;

  final db = ref.watch(baseLocalProvider).db;
  String? valorDe(String clave) {
    final filas = db.select(
      'SELECT valor FROM sync_estado WHERE clave = ?',
      [clave],
    );
    return filas.isEmpty ? null : filas.single['valor'] as String?;
  }

  final dispositivoId = valorDe('dispositivo_id');
  // El camión sale de `almacenDelVendedorProvider`, que prefiere lo que el
  // servidor publicó por delta sobre lo que trae la credencial. La credencial se
  // reescribe solo con un login en línea, así que es el dato más viejo: si la
  // oficina reasignó el camión mientras el vendedor trabajaba offline, estampar
  // las ventas con el de la credencial las descuenta del camión equivocado.
  final almacen = ref.watch(almacenDelVendedorProvider);
  if (dispositivoId == null || almacen == null) return null;

  return IdentidadDeVenta(
    vendedorId: c.usuarioId,
    codigoVendedor: c.codigo,
    dispositivoId: dispositivoId,
    almacenId: almacen,
    cargaId: valorDe('carga_id_activa'),
  );
});

final cierreDeVentaProvider = Provider<CierreDeVenta?>((ref) {
  final identidad = ref.watch(identidadDeVentaProvider);
  if (identidad == null) return null;

  final reloj = ref.watch(relojProvider);
  return CierreDeVenta(
    db: ref.watch(baseLocalProvider).db,
    outbox: ref.watch(outboxProvider),
    folios: ref.watch(repoFoliosProvider),
    nuevoUuid: ref.watch(nuevoUuidProvider),
    ahora: reloj,
    identidad: identidad,
  );
});

/// Lo que pasó al intentar cobrar.
sealed class EstadoCobro {
  const EstadoCobro();
}

class CobroInactivo extends EstadoCobro {
  const CobroInactivo();
}

class CobroEnCurso extends EstadoCobro {
  const CobroEnCurso();
}

class VentaCerrada extends EstadoCobro {
  const VentaCerrada(this.venta);

  final VentaGuardada venta;
}

class CobroFallido extends EstadoCobro {
  const CobroFallido(this.motivo, [this.detalle]);

  final MotivoNoVenta motivo;
  final String? detalle;
}

/// No se pudo ni intentar: falta la identidad del equipo.
class CobroSinIdentidad extends EstadoCobro {
  const CobroSinIdentidad();
}

/// Falló el equipo, no el negocio.
///
/// ─────────────────────────────────────────────────────────────────────────
/// ESTE ESTADO EXISTE PORQUE «NINGÚN ESTADO» ERA PEOR
/// ─────────────────────────────────────────────────────────────────────────
/// Antes, una excepción que no fuera `VentaRechazada` se iba hacia arriba y el
/// estado se quedaba en `CobroEnCurso`: el botón girando, desactivado, y el
/// vendedor sin poder cobrar en todo el día sin matar la app. Un cobro que falla
/// tiene que aterrizar en un estado **terminal** siempre.
class CobroRoto extends EstadoCobro {
  const CobroRoto({required this.quedoEscrita, this.folioLocal});

  /// `true` quedó, `false` no quedó, `null` no se pudo saber. Es lo que decide
  /// si el vendedor vuelve a cobrar. Ver `CierreRoto`.
  final bool? quedoEscrita;

  final String? folioLocal;
}

class ControladorCobro extends Notifier<EstadoCobro> {
  @override
  EstadoCobro build() => const CobroInactivo();

  /// Cierra la venta del carrito actual.
  ///
  /// Si pasa, el carrito se vacía y el borrador se limpia: lo que sigue es
  /// imprimir, y el pedido ya es un documento.
  void cobrar({Ubicacion? ubicacion}) {
    final cierre = ref.read(cierreDeVentaProvider);
    final clienteId = ref.read(clienteEnVisitaProvider);
    if (cierre == null || clienteId == null) {
      state = const CobroSinIdentidad();
      return;
    }

    state = const CobroEnCurso();

    final VentaGuardada venta;
    try {
      venta = cierre.cerrar(
        ref.read(carritoProvider),
        clienteId: clienteId,
        ubicacion: ubicacion,
      );
    } on VentaRechazada catch (e) {
      state = CobroFallido(e.motivo, e.detalle);
      return;
    } on CierreRoto catch (e) {
      state = CobroRoto(
        quedoEscrita: e.quedoEscrita,
        folioLocal: e.folioLocal,
      );
      return;
    } catch (_) {
      // `cerrar` promete no lanzar otra cosa, pero el botón girando para siempre
      // es tan caro que esta red se queda puesta de todos modos.
      state = const CobroRoto(quedoEscrita: null);
      return;
    }

    // De aquí hacia abajo la venta YA es un documento. Nada de lo que sigue
    // puede impedir que el estado llegue a `VentaCerrada`: si la limpieza
    // tronara, el vendedor se quedaría con el botón girando sobre una venta que
    // sí se guardó, y la cobraría otra vez.
    try {
      // El carrito ya es un documento: se vacía para que nadie lo cobre dos
      // veces tocando atrás.
      ref.read(carritoProvider.notifier).vaciar();
      // La lista de clientes y la cola cambiaron: el cliente ya está visitado
      // y hay un sobre nuevo por enviar.
      ref.invalidate(clientesProvider);
      ref.invalidate(resumenColaProvider);
      elCamionCambio(ref);
    } catch (_) {
      // Se sigue de frente: la venta está escrita y la pantalla del ticket es
      // lo que el cliente está esperando.
    }
    state = VentaCerrada(venta);
  }

  void reiniciar() => state = const CobroInactivo();
}

final cobroProvider = NotifierProvider<ControladorCobro, EstadoCobro>(
  ControladorCobro.new,
);

// ---------------------------------------------------------------------------
// La impresión
// ---------------------------------------------------------------------------

/// La impresora del equipo.
///
/// Hoy es la simulada: la EC Line EC-MP200 no está disponible. Cuando llegue, se
/// sustituye ESTE provider por la implementación Bluetooth y **nada más cambia**
/// —ni la pantalla, ni el generador del ticket, ni el registro de la impresión—.
final impresoraProvider = Provider<Impresora>((_) => ImpresoraSimulada());

/// Los datos del negocio que van en el encabezado del ticket.
///
/// Fijos por ahora. Cuando exista el panel de configuración salen de ahí, y el
/// mismo binario sirve para otra distribuidora.
final negocioProvider = Provider<DatosDelNegocio>(
  (_) => const DatosDelNegocio(
    nombre: 'Distribuidora El Ñandú',
    direccion: 'Av. Hidalgo 145, Col. Centro',
    telefono: '55 1234 5678',
    leyendaFinal: 'Gracias por su compra',
  ),
);

/// Arma el ticket de una venta ya guardada.
///
/// El ticket del original se congela en `ventas.ticket_escpos`; las
/// reimpresiones **reusan esos bytes** con el aviso de copia encima, para que el
/// papel diga exactamente lo mismo que el que firmó el cliente.
List<int> armarTicket(
  WidgetRef ref,
  VentaGuardada venta, {
  required String nombreCliente,
  String? codigoCliente,
  String? direccionCliente,
}) {
  final sesion = ref.read(sesionProvider);
  final vendedor =
      sesion is SesionAbierta ? sesion.credencial.nombre : 'Vendedor';

  return ticketDeVenta(
    venta,
    negocio: ref.read(negocioProvider),
    visita: DatosDeLaVisita(
      nombreCliente: nombreCliente,
      nombreVendedor: vendedor,
      codigoCliente: codigoCliente,
      direccionCliente: direccionCliente,
    ),
  );
}
