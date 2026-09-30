/// Alta de cliente en la calle.
///
/// Escribe el cliente en la base local y su sobre en la cola **en una sola
/// transacción de SQLite**, igual que una venta. Si el teléfono se apaga entre
/// las dos escrituras no queda ni el cliente sin sobre ni el sobre sin cliente.
///
/// El UUID lo genera el dispositivo y es la llave primaria también en
/// PostgreSQL: reenviar el sobre no crea un segundo cliente.
///
/// Un cliente de campo nace **sin línea de crédito** (ADR 0002). Esa decisión es
/// de la oficina; aceptarla desde el dispositivo sería dejar que el vendedor se
/// autorice su propia cartera.
library;

import 'package:sqlite3/sqlite3.dart';

import 'outbox.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// Datos que el vendedor captura en la calle.
class DatosDeAlta {
  const DatosDeAlta({
    required this.nombreComercial,
    this.contactoNombre,
    this.telefono,
    this.calle,
    this.numero,
    this.colonia,
    this.referencias,
    this.canalCodigo,
    this.ubicacion,
  });

  final String nombreComercial;
  final String? contactoNombre;
  final String? telefono;
  final String? calle;
  final String? numero;
  final String? colonia;

  /// "Frente al parque, portón café". En colonias sin nomenclatura es más útil
  /// que la dirección formal, y muchas veces es lo único que hay.
  final String? referencias;
  final String? canalCodigo;

  /// Puede ser nula: el GPS no bloquea el alta.
  final Ubicacion? ubicacion;

  String get nombreLimpio => nombreComercial.trim();

  bool get esValido => nombreLimpio.isNotEmpty;

  String get direccionArmada => [calle, numero, colonia]
      .whereType<String>()
      .map((s) => s.trim())
      .where((s) => s.isNotEmpty)
      .join(' ');

  /// Lo que viaja en la operación `cliente.crear`.
  ///
  /// Solo se incluyen los campos con valor: el formato canónico omite las
  /// claves nulas, así que mandarlas explícitamente no cambia el hash pero sí
  /// engorda el payload.
  Map<String, Object?> aPayload() => {
        'nombre_comercial': nombreLimpio,
        if (_hay(contactoNombre)) 'contacto_nombre': contactoNombre!.trim(),
        if (_hay(telefono)) 'telefono': telefono!.trim(),
        if (_hay(calle)) 'calle': calle!.trim(),
        if (_hay(numero)) 'numero': numero!.trim(),
        if (_hay(colonia)) 'colonia': colonia!.trim(),
        if (_hay(referencias)) 'referencias': referencias!.trim(),
        if (_hay(canalCodigo)) 'canal_codigo': canalCodigo!.trim(),
        ...?ubicacion?.aPayload(),
      };

  static bool _hay(String? v) => v != null && v.trim().isNotEmpty;
}

/// Un cliente cercano que podría ser el mismo negocio.
class PosibleDuplicado {
  const PosibleDuplicado({
    required this.clienteId,
    required this.nombreComercial,
    required this.distanciaMetros,
    required this.rumboGrados,
  });

  final String clienteId;
  final String nombreComercial;
  final double distanciaMetros;

  /// Grados desde el norte (0 = norte, 90 = este).
  ///
  /// Con distancia y rumbo, el lienzo espacial coloca cada cliente conocido
  /// alrededor del vendedor **sin descargar un mosaico de mapa**. Un mapa con
  /// calles necesita red, y la corrección se hace justo donde no la hay.
  final double rumboGrados;
}

/// Radio para sospechar de un duplicado.
///
/// Atajarlo aquí vale mucho más que resolverlo después: en la calle el vendedor
/// sabe si la tienda de al lado es la misma o no, y en la oficina —dos semanas
/// después, con dos historiales de venta ya separados— nadie puede saberlo.
const radioDuplicadoMetros = 60.0;

class AltaDeClientes {
  AltaDeClientes({
    required Database db,
    required Outbox outbox,
    required String Function() nuevoUuid,
    required String Function() ahora,
  })  : _db = db,
        _outbox = outbox,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora;

  final Database _db;
  final Outbox _outbox;
  final String Function() _nuevoUuid;
  final String Function() _ahora;

  /// Clientes ya conocidos a menos de [radio] metros, del más cercano al más
  /// lejano.
  ///
  /// Sin ubicación no se puede opinar: se devuelve vacío en vez de comparar por
  /// nombre, que en abarrotes daría falsos positivos constantes ("Abarrotes
  /// María" hay uno por cuadra).
  List<PosibleDuplicado> cercanos(
    Ubicacion? ubicacion, {
    double radio = radioDuplicadoMetros,
  }) {
    if (ubicacion == null) return const [];

    final filas = _db.select(
      'SELECT id, nombre_comercial, lat, lng FROM clientes '
      'WHERE lat IS NOT NULL AND lng IS NOT NULL',
    );

    final cerca = <PosibleDuplicado>[];
    for (final f in filas) {
      final otra = Ubicacion(
        lat: (f['lat'] as num).toDouble(),
        lng: (f['lng'] as num).toDouble(),
        origen: OrigenUbicacion.gps,
      );
      final distancia = ubicacion.distanciaA(otra);
      if (distancia <= radio) {
        cerca.add(
          PosibleDuplicado(
            clienteId: f['id'] as String,
            nombreComercial: f['nombre_comercial'] as String,
            distanciaMetros: distancia,
            rumboGrados: ubicacion.rumboA(otra),
          ),
        );
      }
    }
    cerca.sort((a, b) => a.distanciaMetros.compareTo(b.distanciaMetros));
    return cerca;
  }

  /// Da de alta el cliente y encola su sobre. Devuelve el id generado.
  String registrar(DatosDeAlta datos) {
    if (!datos.esValido) {
      throw ArgumentError('el cliente necesita al menos un nombre comercial');
    }

    final clienteId = _nuevoUuid();
    final momento = _ahora();

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      // La visita agrupa lo que pase en este punto de venta. Hoy solo el alta;
      // cuando exista el carrito, la venta entra en el mismo sobre y se aplica
      // con el cliente en una transacción del lado del servidor.
      visitaId: _nuevoUuid(),
      operaciones: [
        OperacionLocal(
          tipo: 'cliente.crear',
          entidadId: clienteId,
          datos: datos.aPayload(),
        ),
      ],
    );

    _outbox.encolar(
      sobre,
      creadoEn: momento,
      escribirNegocio: (db) => db.execute(
        '''
        INSERT INTO clientes (id, nombre_comercial, telefono, direccion,
                              referencias, lat, lng, ubicacion_precision_m,
                              ubicacion_origen, permite_credito, limite_credito,
                              saldo_cache, bloqueado, es_local, sincronizado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 1, 0)
        ''',
        [
          clienteId,
          datos.nombreLimpio,
          datos.telefono?.trim(),
          datos.direccionArmada.isEmpty ? null : datos.direccionArmada,
          datos.referencias?.trim(),
          datos.ubicacion?.lat,
          datos.ubicacion?.lng,
          datos.ubicacion?.precisionMetros,
          datos.ubicacion?.origen.codigo,
        ],
      ),
    );

    return clienteId;
  }
}
