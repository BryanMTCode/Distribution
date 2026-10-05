/// Andamiaje para las pruebas de widget.
///
/// Monta la app con una base en memoria y un almacén seguro falso, así que la
/// suite corre sin emulador, sin Keystore y en segundos.
library;

import 'dart:convert';
import 'dart:io';

import 'package:dsd_app/src/app.dart';
import 'package:dsd_app/src/datos/almacen_seguro.dart';
import 'package:dsd_app/src/datos/base_local.dart';
import 'package:dsd_app/src/estado/sesion.dart';
import 'package:dsd_core/dsd_core.dart';
import 'package:sqlite3/sqlite3.dart' as sql;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Hash Argon2id real, generado por el servidor. Se lee de los vectores
/// compartidos para que la prueba ejerza el contrato de verdad y no un hash
/// inventado.
final vectorArgon2 = () {
  final documento = jsonDecode(
    File('../../contracts/argon2_vectors.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  final vectores = (documento['vectores'] as List).cast<Map<String, dynamic>>();
  return vectores.firstWhere((v) => v['nombre'] == 'pin_numerico');
}();

String get pinCorrecto => vectorArgon2['password'] as String;
String get pinIncorrecto => vectorArgon2['password_incorrecta'] as String;

Map<String, Object?> credencialDelServidor({
  String rol = 'vendedor',
  String validaHasta = '2026-12-31T00:00:00.000Z',
  List<String> permisos = const ['ventas.crear', 'clientes.crear', 'cobranza.ver'],
}) =>
    {
      'usuario_id': '019283a0-0001-7000-8000-000000000001',
      'codigo': 'VEND01',
      'nombre': 'Juan Pérez',
      'rol': rol,
      'password_hash': vectorArgon2['hash_phc'],
      'permisos': permisos,
      'almacen_id': '019283a0-0002-7000-8000-000000000002',
      'valida_hasta': validaHasta,
    };

/// Inserta un cliente en la base local, como lo dejaría un pull.
void sembrarCliente(
  BaseLocal base, {
  required String id,
  required String nombre,
  int? secuencia,
  String? codigo,
  bool permiteCredito = true,
  double limite = 5000,
  double saldoCache = 0,
  bool bloqueado = false,
  bool esLocal = false,
  double? lat,
  double? lng,
}) {
  base.db.execute(
    '''
    INSERT INTO clientes (id, codigo, nombre_comercial, secuencia, permite_credito,
                          limite_credito, saldo_cache, saldo_cache_en, bloqueado,
                          es_local, sincronizado, lat, lng)
    VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-24T07:00:00.000Z', ?, ?, 1, ?, ?)
    ''',
    [id, codigo, nombre, secuencia, permiteCredito ? 1 : 0, limite, saldoCache,
     bloqueado ? 1 : 0, esLocal ? 1 : 0, lat, lng],
  );
}

/// Siembra una lista de precios, como la dejaría un pull.
void sembrarListaPrecios(
  BaseLocal base, {
  String id = 'lista-general',
  String codigo = 'GENERAL',
  String nombre = 'General',
  bool esDefault = true,
}) {
  base.db.execute(
    'INSERT INTO listas_precios (id, codigo, nombre, es_default, activo) '
    'VALUES (?, ?, ?, ?, 1)',
    [id, codigo, nombre, esDefault ? 1 : 0],
  );
}

/// Siembra un producto con sus presentaciones y precios.
///
/// `precioCaja` en null = el producto solo se vende por pieza. Las dos
/// presentaciones existen porque el caso interesante es justamente que compitan
/// por la misma existencia del camión.
void sembrarProducto(
  BaseLocal base, {
  required String id,
  required String nombre,
  String? sku,
  String listaId = 'lista-general',
  double precioPieza = 13.5,
  double? precioCaja,
  int piezasPorCaja = 24,
  double tasaIva = 0,
  String? codigoBarras,
}) {
  base.db.execute(
    'INSERT INTO productos (id, sku, codigo_barras, nombre, unidad_base, '
    'tasa_iva, activo) VALUES (?, ?, ?, ?, ?, ?, 1)',
    [id, sku ?? id.toUpperCase(), codigoBarras, nombre, 'PZA', tasaIva],
  );
  base.db.execute(
    'INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, '
    'es_default) VALUES (?, ?, 1, 1)',
    [id, 'PZA'],
  );
  base.db.execute(
    'INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version) '
    'VALUES (?, ?, ?, ?, 7)',
    [listaId, id, 'PZA', precioPieza],
  );

  if (precioCaja != null) {
    base.db.execute(
      'INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, '
      'es_default) VALUES (?, ?, ?, 0)',
      [id, 'CAJA', piezasPorCaja],
    );
    base.db.execute(
      'INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, '
      'version) VALUES (?, ?, ?, ?, 7)',
      [listaId, id, 'CAJA', precioCaja],
    );
  }
}

/// Pone existencia del producto arriba del camión.
///
/// Sin fila en esta tabla el producto **no va en la carga** y no se puede
/// vender: eso es a propósito, y hay prueba de ello.
void sembrarCarga(
  BaseLocal base, {
  required String productoId,
  required double unidadesBase,
  String cargaId = 'carga-del-dia',
}) {
  base.db.execute(
    'INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, '
    'carga_id) VALUES (?, ?, ?, ?)',
    [productoId, unidadesBase, unidadesBase, cargaId],
  );
}

/// Un cliente con catálogo listo para venderle: lista, un producto en dos
/// presentaciones y carga en el camión.
///
/// Es el escenario de la mayoría de las pruebas de catálogo y carrito.
void sembrarEscenarioDeVenta(
  BaseLocal base, {
  String clienteId = 'cliente-1',
  String nombreCliente = 'Abarrotes Doña Mary',
  bool permiteCredito = true,
  double limite = 5000,
  double saldoCache = 0,
  bool bloqueado = false,
  double existenciaSopa = 240,
  bool conListaEnCliente = true,
}) {
  sembrarListaPrecios(base);
  sembrarCliente(
    base,
    id: clienteId,
    nombre: nombreCliente,
    secuencia: 1,
    codigo: 'C-001',
    permiteCredito: permiteCredito,
    limite: limite,
    saldoCache: saldoCache,
    bloqueado: bloqueado,
  );
  if (conListaEnCliente) {
    base.db.execute(
      'UPDATE clientes SET lista_precios_id = ? WHERE id = ?',
      ['lista-general', clienteId],
    );
  }
  sembrarProducto(
    base,
    id: 'p-sopa',
    nombre: 'Sopa de fideo 70 g',
    sku: 'SOPA-70G',
    // 296.00 / 24 = 12.3333 por pieza: el caso que justifica los 4 decimales.
    precioPieza: 12.3333,
    precioCaja: 296,
  );
  sembrarCarga(base, productoId: 'p-sopa', unidadesBase: existenciaSopa);
}

/// Lo que hace falta para poder COBRAR: el equipo identificado y su rango de
/// folios.
///
/// Sin esto, cerrar una venta falla con 'sin_rango_de_folios' — que es el
/// comportamiento correcto y tiene su propia prueba.
void sembrarParaCobrar(
  BaseLocal base, {
  String dispositivoId = 'dispositivo-de-prueba',
  String? cargaId = 'carga-del-dia',
  int desde = 1,
  int hasta = 500,
  int consumidoHasta = 0,
}) {
  base.db.execute(
    "INSERT INTO sync_estado (clave, valor) VALUES ('dispositivo_id', ?) "
    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
    [dispositivoId],
  );
  if (cargaId != null) {
    base.db.execute(
      "INSERT INTO sync_estado (clave, valor) VALUES ('carga_id_activa', ?) "
      "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
      [cargaId],
    );
  }
  RepoFolios(base.db).guardar(
    RangoFolios(
      tipo: 'venta',
      desde: desde,
      hasta: hasta,
      consumidoHasta: consumidoHasta,
    ),
    asignadoEn: '2026-09-24T07:00:00.000Z',
  );
}

/// Consultas cortas sobre la base de una prueba.
class BaseLocalDePrueba {
  const BaseLocalDePrueba(this.base);

  final BaseLocal base;

  int contar(String tabla) =>
      base.db.select('SELECT COUNT(*) AS n FROM $tabla').single['n'] as int;

  sql.Row unaFila(String consulta) => base.db.select(consulta).single;
}

/// Venta a crédito encolada y sin sincronizar. Es la que tiene que bajar el
/// disponible que ve el vendedor.
void sembrarVentaACreditoPendiente(
  BaseLocal base, {
  required String clienteId,
  required double total,
  int consecutivo = 1,
}) {
  base.db.execute(
    '''
    INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo,
                        estado, total, fecha_dispositivo, fecha_operativa,
                        sincronizada, creado_en)
    VALUES (?, ?, ?, ?, 'credito', 'confirmada', ?,
            '2026-09-24T10:00:00.000Z', '2026-09-24', 0, '2026-09-24T10:00:00.000Z')
    ''',
    [
      'venta-$consecutivo',
      consecutivo,
      'VEND01-${consecutivo.toString().padLeft(6, '0')}',
      clienteId,
      total,
    ],
  );
}

void sembrarCobroPendiente(
  BaseLocal base, {
  required String clienteId,
  required double importe,
  int consecutivo = 1,
}) {
  base.db.execute(
    '''
    INSERT INTO cobros (id, folio_consecutivo, folio_local, cliente_id, importe,
                        forma_pago, estado, fecha_dispositivo, fecha_operativa,
                        sincronizado, creado_en)
    VALUES (?, ?, ?, ?, ?, 'efectivo', 'confirmado',
            '2026-09-24T11:00:00.000Z', '2026-09-24', 0, '2026-09-24T11:00:00.000Z')
    ''',
    [
      'cobro-$consecutivo',
      consecutivo,
      'VEND01-C${consecutivo.toString().padLeft(5, '0')}',
      clienteId,
      importe,
    ],
  );
}

/// Encola sobres con el payload REAL que viaja al servidor.
///
/// Un payload de relleno (`{}`) haría pasar pruebas que en producción
/// fallarían: el sincronizador manda ese JSON tal cual, y el servidor espera
/// encontrar ahí el `operacion_id`.
void sembrarPendienteEnCola(
  BaseLocal base, {
  int cuantos = 1,
  bool enCuarentena = false,
}) {
  for (var i = 0; i < cuantos; i++) {
    final sobre = SobreLocal(
      operacionId: 'op-$i',
      secuencia: i,
      visitaId: 'visita-$i',
      operaciones: [
        OperacionLocal(
          tipo: 'cliente.crear',
          entidadId: 'ent-$i',
          datos: {'nombre_comercial': 'Tienda $i'},
        ),
      ],
    );
    base.db.execute(
      '''
      INSERT INTO outbox (operacion_id, tipo, entidad_id, payload, hash_payload,
                          secuencia, visita_id, estado, intentos, creado_en)
      VALUES (?, 'cliente.crear', ?, ?, ?, ?, ?, ?, 0,
              '2026-09-24T09:00:00.000Z')
      ''',
      [
        sobre.operacionId,
        'ent-$i',
        jsonEncode(sobre.aMapa()),
        sobre.hash,
        i,
        sobre.visitaId,
        enCuarentena ? 'cuarentena' : 'pendiente',
      ],
    );
  }
}

/// Un rango de folios de la serie que se le pida.
///
/// Cada tipo de documento tiene la suya: si venta, cobro, merma y no-drop
/// compartieran contador, un recibo y una remisión podrían traer el mismo número
/// impreso, y el cliente tendría dos papeles distintos con el mismo folio.
void sembrarFolios(
  BaseLocal base, {
  required String tipo,
  int desde = 1,
  int hasta = 500,
  int consumidoHasta = 0,
}) {
  RepoFolios(base.db).guardar(
    RangoFolios(
      tipo: tipo,
      desde: desde,
      hasta: hasta,
      consumidoHasta: consumidoHasta,
    ),
    asignadoEn: '2026-09-24T07:00:00.000Z',
  );
}

void sembrarFoliosDeCobro(
  BaseLocal base, {
  int desde = 1,
  int hasta = 500,
  int consumidoHasta = 0,
}) =>
    sembrarFolios(
      base,
      tipo: 'cobro',
      desde: desde,
      hasta: hasta,
      consumidoHasta: consumidoHasta,
    );

/// Los catálogos cerrados de motivos, como los dejaría un delta del servidor.
///
/// Sin ellos las pantallas de merma y no-drop se niegan a capturar —y hay prueba
/// de eso—, así que casi toda prueba de esas dos pantallas empieza aquí.
void sembrarMotivos(BaseLocal base) {
  base.db.execute(
    'INSERT INTO motivos_merma (codigo, nombre, afecta_vendedor, activo) VALUES '
    "('CADUCADO', 'Producto caducado', 0, 1), "
    "('ROTO', 'Empaque roto', 1, 1), "
    "('DEVOLUCION_CLIENTE', 'Devolución del cliente', 0, 1)",
  );
  base.db.execute(
    'INSERT INTO motivos_no_drop (codigo, nombre, categoria, requiere_nota, '
    'orden, activo) VALUES '
    "('CERRADO', 'Cerrado', 'cliente', 0, 10, 1), "
    "('AGOTADO_EN_CAMION', 'No traigo lo que pidió', 'producto', 1, 70, 1)",
  );
}

/// Monta la app completa con dependencias de prueba.
///
/// Devuelve la base para poder sembrar datos y verificar efectos.
Future<BaseLocal> montarApp(
  WidgetTester tester, {
  Map<String, Object?>? credencial,
  DateTime? ahora,
  void Function(BaseLocal base)? sembrar,
  List<Override> extras = const [],
  /// Un almacén seguro propio, para poder inspeccionarlo después de la prueba.
  ///
  /// Lo necesitan las pruebas del borrado remoto: hay que comprobar que la
  /// credencial y la llave de la base DESAPARECIERON, y con el almacén interno
  /// no hay forma de mirarlo desde fuera.
  AlmacenSeguroEnMemoria? almacenPropio,
}) async {
  final base = BaseLocal.enMemoria();
  final almacen = almacenPropio ?? AlmacenSeguroEnMemoria();
  if (credencial != null) {
    await almacen.escribir('credencial_local_v1', jsonEncode(credencial));
  }
  sembrar?.call(base);

  addTearDown(base.cerrar);

  // Viewport con forma de teléfono. El 800x600 que trae flutter_test por
  // defecto es apaisado y bajo: deja fuera de pantalla lo que en un equipo real
  // se ve sin desplazar, y hace fallar pruebas por una razón que no existe en
  // la calle.
  tester.view.physicalSize = const Size(400, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        almacenSeguroProvider.overrideWithValue(almacen),
        baseLocalProvider.overrideWithValue(base),
        relojProvider.overrideWithValue(() => ahora ?? relojDePrueba()),
        ...extras,
      ],
      child: const AppDsd(),
    ),
  );
  await tester.pumpAndSettle();
  return base;
}

/// Teclea el PIN y espera a que Argon2 termine.
///
/// La verificación tarda cientos de milisegundos a propósito: es lo que hace
/// caro probar PINs en un teléfono robado.
Future<void> entrarCon(WidgetTester tester, String pin) async {
  await tester.enterText(find.byKey(const Key('campo_pin')), pin);
  await tester.tap(find.byKey(const Key('boton_entrar')));
  await tester.pumpAndSettle(const Duration(seconds: 5));
}

/// Toca un control asegurándose de que esté a la vista.
///
/// En un formulario largo, `tap` sobre algo que quedó fuera del área visible
/// —o detrás de la barra fija— no llega al widget y la prueba falla por una
/// razón que no tiene que ver con lo que se está probando.
Future<void> tocar(WidgetTester tester, Key clave) async {
  final finder = find.byKey(clave);
  await traerAlArbol(tester, finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

/// Trae un widget al árbol, desplazando si hace falta.
///
/// En un formulario largo un `ListView` **desecha** los hijos que quedan lejos
/// del área visible. Si la prueba se desplazó hacia abajo —para tocar los botones
/// de ajuste, por ejemplo— el campo de arriba ya no existe, y tanto `enterText`
/// como `ensureVisible` fallan con un "Bad state: No element" que no dice nada
/// del código.
///
/// `scrollUntilVisible` sí sabe buscar fuera del árbol: desplaza hasta
/// encontrarlo. Se intenta primero hacia arriba, que es de donde vienen los
/// campos que la prueba ya llenó.
Future<void> traerAlArbol(WidgetTester tester, Finder finder) async {
  if (finder.evaluate().isNotEmpty) {
    await tester.ensureVisible(finder);
    await tester.pumpAndSettle();
    return;
  }
  final desplazable = find.byType(Scrollable).first;
  for (final delta in [-120.0, 120.0]) {
    try {
      await tester.scrollUntilVisible(finder, delta, scrollable: desplazable);
      await tester.pumpAndSettle();
      return;
    } on Object {
      // Se intenta en la otra dirección.
    }
  }
}

/// Escribe en un campo, trayéndolo al árbol si el desplazamiento lo desechó.
Future<void> escribirEn(WidgetTester tester, Key clave, String texto) async {
  final finder = find.byKey(clave);
  await traerAlArbol(tester, finder);
  await tester.enterText(finder, texto);
  await tester.pumpAndSettle();
}

/// Toca un control que muestra un indicador de progreso mientras trabaja.
///
/// `pumpAndSettle` espera a que **no quede ninguna animación**, y un
/// `CircularProgressIndicator` gira para siempre: la prueba se queda colgada con
/// un "pumpAndSettle timed out" que no dice nada del código. Aquí se avanza el
/// reloj a pasos fijos, que es lo que hace falta para que el `await` de la
/// operación termine.
Future<void> tocarConProgreso(
  WidgetTester tester,
  Key clave, {
  Duration espera = const Duration(milliseconds: 150),
}) async {
  final finder = find.byKey(clave);
  await tester.ensureVisible(finder);
  await tester.pump();
  // El toque va DENTRO de `runAsync`, que corre en la zona asíncrona real.
  //
  // Sin esto, el trabajo que toca el disco —la impresora simulada guarda el
  // ticket en un archivo— nunca termina: `testWidgets` usa un reloj falso que
  // adelanta timers pero no completa la entrada/salida de verdad. El síntoma es
  // un `await` colgado y una aserción que falla como si el código no hubiera
  // hecho nada, sin ninguna pista de por qué.
  await tester.runAsync(() async {
    await tester.tap(finder);
    await Future<void>.delayed(espera);
  });
  await tester.pump();
  await tester.pump();
}

Finder textoQueContiene(String fragmento) => find.byWidgetPredicate(
      (w) => w is Text && (w.data ?? '').contains(fragmento),
    );

/// Lee el texto de un `Text` que trae la llave puesta **en él mismo**.
///
/// `find.descendant(of: find.byKey(k), matching: find.text(...))` no sirve para
/// estos casos: el widget con la llave ES el `Text`, no un ancestro suyo, y la
/// búsqueda devuelve cero sin que quede claro por qué.
String textoDe(WidgetTester tester, Key clave) =>
    (tester.widget(find.byKey(clave)) as Text).data ?? '';

/// Una venta del día de hoy en la base local, como la dejaría el carrito.
///
/// La fecha se deriva igual que en la app —los diez primeros caracteres del
/// instante UTC— para que la pantalla y el dato coincidan.
/// El reloj de las pruebas, en un solo lugar.
///
/// Lo usan el override del provider Y los sembradores de ventas y cobros: si cada
/// uno tuviera su propia constante, un sembrador podría estampar una fecha
/// operativa distinta de la que la pantalla consulta y la prueba fallaría por una
/// razón que no tiene nada que ver con lo que prueba.
DateTime relojDePrueba() => DateTime.utc(2026, 9, 24, 7);

void sembrarVentaDelDia(
  BaseLocal base, {
  required String folio,
  required double total,
  String tipo = 'contado',
  String cliente = 'c1',
  int sincronizada = 0,
}) {
  final momento = relojDePrueba().toUtc().toIso8601String();
  base.db.execute(
    'INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo, '
    'estado, total, fecha_dispositivo, fecha_operativa, sincronizada, creado_en) '
    "VALUES (?, ?, ?, ?, ?, 'confirmada', ?, ?, ?, ?, ?)",
    [folio, folio.hashCode.abs(), folio, cliente, tipo, total, momento,
     momento.substring(0, 10), sincronizada, momento],
  );
}

/// Un cobro del día de hoy.
void sembrarCobroDelDia(
  BaseLocal base, {
  required String folio,
  required double importe,
  String forma = 'efectivo',
  String cliente = 'c1',
}) {
  final momento = relojDePrueba().toUtc().toIso8601String();
  base.db.execute(
    'INSERT INTO cobros (id, folio_consecutivo, folio_local, cliente_id, importe, '
    "forma_pago, estado, fecha_dispositivo, fecha_operativa, creado_en) "
    "VALUES (?, ?, ?, ?, ?, ?, 'confirmado', ?, ?, ?)",
    [folio, folio.hashCode.abs(), folio, cliente, importe, forma, momento,
     momento.substring(0, 10), momento],
  );
}
