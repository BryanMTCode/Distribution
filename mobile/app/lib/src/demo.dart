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

/// Un cliente de demostración.
class _ClienteDemo {
  const _ClienteDemo(
    this.id,
    this.nombre, {
    required this.secuencia,
    this.limite = 0,
    this.saldo = 0,
    this.permiteCredito = false,
    this.bloqueado = false,
    this.metrosAlNorte = 0,
    this.metrosAlEste = 0,
    this.ventaPendiente = 0,
    this.direccion,
  });

  final String id;
  final String nombre;
  final int secuencia;
  final double limite;
  final double saldo;
  final bool permiteCredito;
  final bool bloqueado;

  /// Desplazamiento respecto del punto de referencia. Sirve para que el aviso
  /// de posible duplicado se pueda disparar de verdad en campo.
  final double metrosAlNorte;
  final double metrosAlEste;

  /// Venta a crédito sin sincronizar, para ver el disponible ya descontado.
  final double ventaPendiente;
  final String? direccion;
}

/// Los casos que vale la pena ver en pantalla, no una lista de relleno.
const _clientesDemo = [
  _ClienteDemo(
    'demo-01',
    'Abarrotes Doña Mary',
    secuencia: 1,
    permiteCredito: true,
    limite: 5000,
    saldo: 1200,
    direccion: 'Av. Hidalgo 145, Centro',
    metrosAlNorte: 25,
  ),
  _ClienteDemo(
    'demo-02',
    'La Esquina de Ñoño 🏪',
    secuencia: 2,
    permiteCredito: true,
    limite: 3000,
    saldo: 900,
    // Con una venta encolada: el disponible en pantalla ya debe estar
    // descontado sin haber sincronizado nada.
    ventaPendiente: 1500,
    direccion: 'Morelos 22',
    metrosAlNorte: 45,
    metrosAlEste: 20,
  ),
  _ClienteDemo(
    'demo-03',
    'Tienda del Mercado, local 12',
    secuencia: 3,
    permiteCredito: true,
    limite: 1000,
    saldo: 1000, // crédito agotado
    direccion: 'Mercado Juárez',
    metrosAlNorte: 300,
  ),
  _ClienteDemo(
    'demo-04',
    'Miscelánea El Buen Precio',
    secuencia: 4,
    permiteCredito: true,
    limite: 2000,
    bloqueado: true,
    direccion: 'Calle 5 de Mayo 8',
    metrosAlNorte: 600,
  ),
  _ClienteDemo(
    'demo-05',
    'Cremería Los Compadres',
    secuencia: 5,
    direccion: 'Sin crédito, solo contado',
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

  for (final c in _clientesDemo) {
    final punto = base.desplazada(norte: c.metrosAlNorte, este: c.metrosAlEste);
    db.execute(
      '''
      INSERT INTO clientes (id, codigo, nombre_comercial, direccion, secuencia,
                            lat, lng, ubicacion_origen, permite_credito,
                            limite_credito, saldo_cache, saldo_cache_en,
                            bloqueado, es_local, sincronizado)
      VALUES (?, ?, ?, ?, ?, ?, ?, 'gps', ?, ?, ?, ?, ?, 0, 1)
      ON CONFLICT(id) DO UPDATE SET
        lat = excluded.lat,
        lng = excluded.lng,
        saldo_cache = excluded.saldo_cache,
        saldo_cache_en = excluded.saldo_cache_en
      ''',
      [
        c.id,
        'DEMO-${c.secuencia.toString().padLeft(3, '0')}',
        c.nombre,
        c.direccion,
        c.secuencia,
        punto.lat,
        punto.lng,
        c.permiteCredito ? 1 : 0,
        c.limite,
        c.saldo,
        ahora,
        c.bloqueado ? 1 : 0,
      ],
    );

    if (c.ventaPendiente > 0) {
      db.execute(
        '''
        INSERT INTO ventas (id, folio_consecutivo, folio_local, cliente_id, tipo,
                            estado, total, fecha_dispositivo, fecha_operativa,
                            sincronizada, creado_en)
        VALUES (?, ?, ?, ?, 'credito', 'confirmada', ?, ?, ?, 0, ?)
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
