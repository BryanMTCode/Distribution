/// Modo demo: siembra una sesión y datos locales para evaluar la interfaz.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ NO PUEDE LLEGAR A PRODUCCIÓN
/// ─────────────────────────────────────────────────────────────────────────
/// Un atajo que salta el login es exactamente lo que no debe existir en el
/// teléfono de un vendedor. Por eso la puerta tiene **dos cerrojos y los dos
/// son de compilación**:
///
///   1. `bool.fromEnvironment('DSD_DEMO')` — hay que pedirlo explícitamente al
///      compilar. Sin el `--dart-define`, la constante es `false`.
///   2. `!kReleaseMode` — aunque alguien pase el define en un build de release,
///      se ignora.
///
/// Las dos son `const`, así que el compilador de Dart elimina este código del
/// árbol en cualquier build de release: no es que el botón esté oculto, es que
/// no existe en el binario.
///
/// Uso:
///     flutter run --dart-define=DSD_DEMO=true
///     flutter build apk --debug --dart-define=DSD_DEMO=true
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/foundation.dart';
import 'package:sqlite3/sqlite3.dart';

/// `true` solo en un build de depuración al que se le pidió el modo demo.
const modoDemoDisponible =
    !kReleaseMode && bool.fromEnvironment('DSD_DEMO');

/// PIN del vendedor de demostración.
const pinDemo = '481507';

/// Hash Argon2id que corresponde a [pinDemo].
///
/// Es uno de los vectores compartidos —lo generó el servidor con
/// `argon2-cffi`—, así que el login de demo recorre **el camino real de
/// verificación**, no un atajo. `test/demo_test.dart` comprueba que siga
/// coincidiendo con `contracts/argon2_vectors.json`.
const hashDemo =
    r'$argon2id$v=19$m=65536,t=3,p=4$Qhx69MQP6H1QG36ZSboKrg$mYj6WV6u1z2GurfVy2Djco3uepFlHgT5pKpusG6PU8w';

/// La credencial que se guarda para permitir el login sin señal.
Map<String, Object?> credencialDemo({required DateTime ahora}) => {
      'usuario_id': '019283f0-0001-7000-8000-000000000001',
      'codigo': 'DEMO01',
      'nombre': 'Vendedor de prueba',
      'rol': 'vendedor',
      'password_hash': hashDemo,
      'permisos': const [
        'catalogo.ver',
        'clientes.ver',
        'clientes.crear',
        'ventas.crear',
        'cobranza.ver',
        'cobranza.registrar',
        'inventario.ver',
        'mermas.registrar',
        'nodrops.registrar',
      ],
      'almacen_id': '019283f0-0002-7000-8000-000000000002',
      // Holgada: nadie quiere que la demo caduque a media evaluación en campo.
      'valida_hasta': ahora.add(const Duration(days: 30)).toUtc().toIso8601String(),
    };

/// Un producto de demostración, con su precio de pieza y de caja.
class _ProductoDemo {
  const _ProductoDemo(
    this.id,
    this.sku,
    this.nombre, {
    required this.precioPieza,
    this.precioCaja,
    this.piezasPorCaja = 24,
    this.existencia = 0,
    this.vaEnLaCarga = true,
  });

  final String id;
  final String sku;
  final String nombre;

  /// Con 4 decimales, como llega del servidor.
  final double precioPieza;
  final double? precioCaja;
  final int piezasPorCaja;

  /// Unidades base arriba del camión.
  final double existencia;

  /// `false` = el producto existe en el catálogo pero no subió al camión hoy.
  /// Es un estado DISTINTO de "agotado" y se ve distinto en pantalla: agotado es
  /// "se vendió todo", no va en la carga es "la bodega no lo subió".
  final bool vaEnLaCarga;
}

/// El catálogo de demostración. Cada producto está por un caso concreto.
const _productosDemo = [
  _ProductoDemo(
    'demo-p-sopa',
    'SOPA-70G',
    'Sopa de fideo 70 g',
    // 296.00 / 24 = 12.3333. EL caso que justifica los 4 decimales: 24 piezas
    // dan 296.00 exactos, no 295.92.
    precioPieza: 12.3333,
    precioCaja: 296,
    existencia: 240,
  ),
  _ProductoDemo(
    'demo-p-frijol',
    'FRIJOL-1K',
    'Frijol bayo 1 kg',
    precioPieza: 32.5,
    precioCaja: 620,
    piezasPorCaja: 20,
    existencia: 60,
  ),
  _ProductoDemo(
    'demo-p-aceite',
    'ACEITE-900',
    'Aceite vegetal 900 ml',
    precioPieza: 38.9,
    precioCaja: 445.5,
    piezasPorCaja: 12,
    // Poca existencia: con 3 cajas se agota, y ahí se ve el aviso de
    // "solo quedan N" con la unidad correcta.
    existencia: 30,
  ),
  _ProductoDemo(
    'demo-p-azucar',
    'AZUCAR-1K',
    'Azúcar estándar 1 kg',
    precioPieza: 27.25,
    existencia: 4,
  ),
  _ProductoDemo(
    'demo-p-atun',
    'ATUN-140',
    'Atún en agua 140 g',
    precioPieza: 19.5,
    precioCaja: 455,
    // Subió al camión pero ya se vendió todo: se ve AGOTADO. Aparece en la
    // lista a propósito — el vendedor necesita saber que existe para pedirlo
    // mañana.
    existencia: 0,
  ),
  _ProductoDemo(
    'demo-p-jabon',
    'JABON-150',
    'Jabón de tocador 150 g',
    precioPieza: 16.75,
    precioCaja: 390,
    // La bodega no lo subió hoy. Estado distinto de "agotado", y se lee
    // distinto en pantalla.
    vaEnLaCarga: false,
  ),
];

/// Un cliente de demostración.
class _ClienteDemo {
  const _ClienteDemo(
    this.id,
    this.nombre, {
    required this.secuencia,
    this.metrosAlNorte = 0,
    this.metrosAlEste = 0,
    this.ventaPendiente = 0,
    this.direccion,
  });

  final String id;
  final String nombre;
  final int secuencia;

  /// Desplazamiento respecto del punto de referencia. Sirve para que el aviso
  /// de posible duplicado se pueda disparar de verdad en campo.
  final double metrosAlNorte;
  final double metrosAlEste;

  /// Venta por transferencia sin sincronizar: se ve en «Mi día» separada del
  /// efectivo y en la barra de lo que falta por subir.
  final double ventaPendiente;
  final String? direccion;
}

/// Los casos que vale la pena ver en pantalla, no una lista de relleno. Todo es
/// de contado (ADR 0002 §81): ya no hay estados de crédito que distinguir.
const _clientesDemo = [
  _ClienteDemo(
    'demo-01',
    'Abarrotes Doña Mary',
    secuencia: 1,
    direccion: 'Av. Hidalgo 145, Centro',
    metrosAlNorte: 25,
  ),
  _ClienteDemo(
    'demo-02',
    'La Esquina de Ñoño 🏪',
    secuencia: 2,
    ventaPendiente: 1500,
    direccion: 'Morelos 22',
    metrosAlNorte: 45,
    metrosAlEste: 20,
  ),
  _ClienteDemo(
    'demo-03',
    'Tienda del Mercado, local 12',
    secuencia: 3,
    direccion: 'Mercado Juárez',
    metrosAlNorte: 300,
  ),
  _ClienteDemo(
    'demo-04',
    'Miscelánea El Buen Precio',
    secuencia: 4,
    direccion: 'Calle 5 de Mayo 8',
    metrosAlNorte: 600,
  ),
  _ClienteDemo(
    'demo-05',
    'Cremería Los Compadres',
    secuencia: 5,
    direccion: 'Ángel Flores 30',
    metrosAlNorte: 900,
  ),
];

/// Siembra los datos de demostración en la base local.
///
/// Idempotente: volver a sembrar no duplica nada, así que se puede pulsar el
/// botón varias veces sin dejar la base en un estado raro.
///
/// [referencia] es el punto desde el que se colocan los clientes. Si se pasa la
/// ubicación real del dispositivo, los vecinos quedan a 25 y 45 metros y el
/// aviso de posible duplicado se puede probar **en el lugar donde estás**, que
/// es lo único que no se puede evaluar en un emulador.
void sembrarDemo(
  Database db, {
  Ubicacion? referencia,
  required String ahora,
}) {
  final base = referencia ??
      Ubicacion(lat: 19.4326, lng: -99.1332, origen: OrigenUbicacion.gps);

  _sembrarCatalogo(db);
  _sembrarParaCobrar(db, ahora: ahora);

  for (final c in _clientesDemo) {
    final punto = base.desplazada(norte: c.metrosAlNorte, este: c.metrosAlEste);
    db.execute(
      '''
      INSERT INTO clientes (id, codigo, nombre_comercial, direccion, secuencia,
                            lat, lng, ubicacion_origen, lista_precios_id,
                            es_local, sincronizado)
      VALUES (?, ?, ?, ?, ?, ?, ?, 'gps', 'demo-lista-general', 0, 1)
      ON CONFLICT(id) DO UPDATE SET
        lat = excluded.lat,
        lng = excluded.lng
      ''',
      [
        c.id,
        'DEMO-${c.secuencia.toString().padLeft(3, '0')}',
        c.nombre,
        c.direccion,
        c.secuencia,
        punto.lat,
        punto.lng,
      ],
    );

    if (c.ventaPendiente > 0) {
      db.execute(
        '''
        INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo,
                            forma_pago, estado, total, fecha_dispositivo,
                            fecha_operativa, sincronizada, creado_en)
        VALUES (?, ?, ?, ?, 'contado', 'transferencia', 'confirmada', ?, ?, ?, 0, ?)
        ON CONFLICT(id) DO NOTHING
        ''',
        [
          'demo-venta-${c.id}',
          900 + c.secuencia,
          'DEMO01-${(900 + c.secuencia).toString().padLeft(6, '0')}',
          c.id,
          c.ventaPendiente,
          ahora,
          ahora.substring(0, 10),
          ahora,
        ],
      );
    }
  }
}

/// Lo que hace falta para poder CERRAR una venta: el equipo identificado y su
/// rango de folios.
///
/// Sin esto el botón de cobrar diría "este equipo no tiene folios asignados",
/// que es el comportamiento correcto en producción —el rango lo asigna el
/// servidor— pero dejaría el modo demo sin poder probar la mitad interesante.
void _sembrarParaCobrar(Database db, {required String ahora}) {
  const estado = {
    'dispositivo_id': '019283f0-0003-7000-8000-000000000003',
    'carga_id_activa': 'demo-carga-del-dia',
  };
  for (final entrada in estado.entries) {
    db.execute(
      'INSERT INTO sync_estado (clave, valor) VALUES (?, ?) '
      'ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor',
      [entrada.key, entrada.value],
    );
  }

  // Un rango corto a propósito: con 30 folios el aviso de "te quedan pocos"
  // aparece a la segunda venta, y así se puede ver en campo sin tener que
  // emitir cientos de tickets.
  db.execute(
    '''
    INSERT INTO folios_rangos (tipo, desde, hasta, consumido_hasta, asignado_en)
    VALUES ('venta', 1, 30, 0, ?)
    ON CONFLICT(tipo) DO NOTHING
    ''',
    [ahora],
  );
}

/// El catálogo y la carga del camión.
///
/// Sin esto el catálogo se vería vacío y el carrito no tendría contra qué
/// descontar, así que no habría nada que evaluar en la pantalla. Todo es
/// idempotente: pulsar el botón dos veces no duplica ni infla la carga.
void _sembrarCatalogo(Database db) {
  db.execute(
    '''
    INSERT INTO listas_precios (id, codigo, nombre, es_default, activo)
    VALUES ('demo-lista-general', 'GENERAL', 'General (demo)', 1, 1)
    ON CONFLICT(id) DO NOTHING
    ''',
  );

  for (final p in _productosDemo) {
    db.execute(
      '''
      INSERT INTO productos (id, sku, nombre, unidad_base, tasa_iva, activo)
      VALUES (?, ?, ?, 'PZA', 0, 1)
      ON CONFLICT(id) DO UPDATE SET nombre = excluded.nombre
      ''',
      [p.id, p.sku, p.nombre],
    );

    // La pieza: unidad base, factor 1, y la presentación por omisión.
    db.execute(
      '''
      INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default)
      VALUES (?, 'PZA', 1, 1)
      ON CONFLICT(producto_id, unidad_codigo) DO NOTHING
      ''',
      [p.id],
    );
    db.execute(
      '''
      INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version)
      VALUES ('demo-lista-general', ?, 'PZA', ?, 7)
      ON CONFLICT(lista_id, producto_id, unidad_codigo)
        DO UPDATE SET precio = excluded.precio
      ''',
      [p.id, p.precioPieza],
    );

    if (p.precioCaja != null) {
      db.execute(
        '''
        INSERT INTO producto_unidades (producto_id, unidad_codigo, factor, es_default)
        VALUES (?, 'CAJA', ?, 0)
        ON CONFLICT(producto_id, unidad_codigo) DO UPDATE SET factor = excluded.factor
        ''',
        [p.id, p.piezasPorCaja],
      );
      db.execute(
        '''
        INSERT INTO precios (lista_id, producto_id, unidad_codigo, precio, version)
        VALUES ('demo-lista-general', ?, 'CAJA', ?, 7)
        ON CONFLICT(lista_id, producto_id, unidad_codigo)
          DO UPDATE SET precio = excluded.precio
        ''',
        [p.id, p.precioCaja],
      );
    }

    // Sin fila en `existencias_camion` el producto NO VA EN LA CARGA, que es
    // distinto de estar agotado: una fila con cero es "se vendió todo".
    if (p.vaEnLaCarga) {
      db.execute(
        '''
        INSERT INTO existencias_camion (producto_id, cant_cargada, cant_actual, carga_id)
        VALUES (?, ?, ?, 'demo-carga-del-dia')
        ON CONFLICT(producto_id) DO UPDATE SET
          cant_cargada = excluded.cant_cargada,
          cant_actual = excluded.cant_actual
        ''',
        [p.id, p.existencia, p.existencia],
      );
    }
  }
}
