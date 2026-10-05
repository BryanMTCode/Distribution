/// La venta: el momento en que el carrito se vuelve un hecho irreversible.
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTA ES LA PIEZA MÁS DELICADA DE LA APP
/// ─────────────────────────────────────────────────────────────────────────
/// Confirmar una venta hace **cinco cosas**, y todas tienen que pasar o ninguna:
///
///   1. Quema un folio del rango asignado (el número que se imprime en papel).
///   2. Escribe la venta y sus partidas.
///   3. Descuenta la mercancía del camión.
///   4. Encola el sobre que la llevará al servidor.
///   5. Avanza la marca del folio consumido.
///
/// Si el teléfono se apaga a la mitad, cada combinación parcial es un problema
/// distinto, y **ninguno se detecta hasta la liquidación del final del día**:
///
///   · Venta sin descuento de inventario → el camión "tiene" mercancía vendida.
///   · Descuento sin venta → faltante sin explicación.
///   · Venta sin sobre → el cliente tiene su papel y la oficina nunca se entera.
///   · Marca de folio avanzada sin venta → hueco en la numeración impresa.
///
/// Todo va en **una transacción de SQLite**.
///
/// ─────────────────────────────────────────────────────────────────────────
/// EL FOLIO: NI HUECO NI DUPLICADO
/// ─────────────────────────────────────────────────────────────────────────
/// El rango se **lee de la base en cada venta**, nunca se guarda en memoria
/// entre ventas, y su marca de consumo se escribe dentro de la misma
/// transacción. Así:
///
///   · Si la transacción se confirma, el folio quedó quemado y el papel existe.
///   · Si se deshace, la marca no avanzó y el siguiente intento **reutiliza el
///     mismo número**.
///
/// Un objeto de rango de larga vida se adelantaría en un rollback y dejaría
/// huecos que nadie podría explicar en una auditoría.
///
/// ─────────────────────────────────────────────────────────────────────────
/// UNA VENTA NUNCA SE EDITA
/// ─────────────────────────────────────────────────────────────────────────
/// El papel ya está en manos del cliente. Corregirla se hace con un documento
/// compensatorio (cancelación), nunca con un UPDATE. Aquí no hay método para
/// modificar una venta guardada, y eso es deliberado.
library;

import 'dart:typed_data';

import 'package:sqlite3/sqlite3.dart';

import 'dia_operativo.dart';
import 'carrito.dart';
import 'dinero.dart';
import 'folios.dart';
import 'outbox.dart';
import 'sobre.dart';
import 'ubicacion.dart';

/// Quién vende, desde dónde, con qué equipo.
class IdentidadDeVenta {
  const IdentidadDeVenta({
    required this.vendedorId,
    required this.codigoVendedor,
    required this.dispositivoId,
    required this.almacenId,
    this.rutaId,
    this.cargaId,
  });

  final String vendedorId;

  /// El prefijo del folio impreso: 'VEND01-000124'.
  final String codigoVendedor;
  final String dispositivoId;

  /// El camión.
  final String almacenId;
  final String? rutaId;
  final String? cargaId;
}

/// Por qué no se pudo cerrar la venta.
///
/// Ninguno es un error de programación: son situaciones reales de la calle, y
/// cada una pide algo distinto del vendedor.
enum MotivoNoVenta {
  /// No hay nada que vender.
  carritoVacio('carrito_vacio'),

  /// El crédito del cliente no lo permite. Se puede cambiar a contado.
  creditoRechazado('credito_rechazado'),

  /// El servidor no ha asignado folios a este equipo. Hay que sincronizar.
  sinRangoDeFolios('sin_rango_de_folios'),

  /// Se acabaron los folios del rango. Hay que sincronizar para pedir otro.
  sinFolios('sin_folios'),

  /// La mercancía ya no está en el camión. Pasa si el catálogo se refrescó
  /// entre que se armó el carrito y se confirmó.
  sinExistencia('sin_existencia');

  const MotivoNoVenta(this.codigo);

  final String codigo;
}

class VentaRechazada implements Exception {
  const VentaRechazada(this.motivo, [this.detalle]);

  final MotivoNoVenta motivo;
  final String? detalle;

  @override
  String toString() =>
      'venta rechazada (${motivo.codigo})${detalle == null ? '' : ': $detalle'}';
}

/// Una venta ya guardada. Es lo que se imprime y lo que se le muestra al
/// vendedor.
class VentaGuardada {
  const VentaGuardada({
    required this.id,
    required this.folioConsecutivo,
    required this.folioLocal,
    required this.visitaId,
    required this.clienteId,
    required this.aCredito,
    required this.subtotal,
    required this.total,
    required this.lineas,
    required this.fechaDispositivo,
    required this.fechaOperativa,
    required this.foliosRestantes,
    this.ubicacion,
  });

  final String id;
  final int folioConsecutivo;

  /// El folio impreso en el papel: 'VEND01-000124'.
  final String folioLocal;
  final String visitaId;
  final String clienteId;
  final bool aCredito;
  final Dinero subtotal;
  final Dinero total;
  final List<LineaCarrito> lineas;
  final String fechaDispositivo;
  final String fechaOperativa;
  final Ubicacion? ubicacion;

  /// Cuántos folios quedan en el rango. Quedarse sin folios a media ruta
  /// significa no poder vender, así que se avisa con holgura.
  final int foliosRestantes;

  int get cuantasLineas => lineas.length;
  bool get pocosFoliosRestantes => foliosRestantes <= 50;
}

/// Cierra ventas.
class CierreDeVenta {
  CierreDeVenta({
    required Database db,
    required Outbox outbox,
    required RepoFolios folios,
    required String Function() nuevoUuid,
    required DateTime Function() ahora,
    required IdentidadDeVenta identidad,
  })  : _db = db,
        _outbox = outbox,
        _folios = folios,
        _nuevoUuid = nuevoUuid,
        _ahora = ahora,
        _identidad = identidad;

  final Database _db;
  final Outbox _outbox;
  final RepoFolios _folios;
  final String Function() _nuevoUuid;
  final DateTime Function() _ahora;
  final IdentidadDeVenta _identidad;

  /// Cierra la venta. O pasa todo, o no pasa nada.
  ///
  /// [creditoPermitido] lo decide el llamador con el estado de crédito ya
  /// compuesto desde la cola local. Se pasa el veredicto y no el estado porque
  /// **la pantalla ya se lo mostró al vendedor**: recalcularlo aquí podría dar
  /// otra respuesta que el vendedor nunca vio.
  VentaGuardada cerrar(
    Carrito carrito, {
    required String clienteId,
    required bool creditoPermitido,
    Ubicacion? ubicacion,
    String? visitaId,
    String? observaciones,
  }) {
    if (carrito.estaVacio) {
      throw const VentaRechazada(MotivoNoVenta.carritoVacio);
    }
    if (carrito.aCredito && !creditoPermitido) {
      throw const VentaRechazada(MotivoNoVenta.creditoRechazado);
    }

    // Se lee de la base, no de un campo: ver la nota del encabezado sobre huecos
    // y duplicados.
    final rango = _folios.leer('venta');
    if (rango == null) {
      throw const VentaRechazada(MotivoNoVenta.sinRangoDeFolios);
    }
    if (rango.agotado) {
      throw VentaRechazada(
        MotivoNoVenta.sinFolios,
        'el rango terminaba en ${rango.hasta}',
      );
    }

    // Una sola lectura del reloj: dos llamadas podrían caer en segundos
    // distintos y estampar un documento con hora de un día y fecha de otro.
    final instante = _ahora();
    final momento = instante.toUtc().toIso8601String();
    final ventaId = _nuevoUuid();
    final visita = visitaId ?? _nuevoUuid();
    // La fecha operativa es la del día de ruta: una venta a las 00:30 pertenece
    // al día que se abrió, no al que dice el calendario.
    // El día LOCAL, no el de `momento` —que está en UTC—: en UTC−6 ese
    // atajo mandaba las ventas de la tarde al día siguiente. Ver
    // `diaOperativoDe`.
    final fechaOperativa = diaOperativoDe(instante);

    // El folio se toma ANTES de armar el sobre, para que el payload viaje
    // completo. Si la transacción falla, la marca persistida no avanza y el
    // siguiente intento vuelve a este mismo número.
    final consecutivo = rango.tomar();
    final folioLocal =
        RangoFolios.formatear(_identidad.codigoVendedor, consecutivo);

    final primera = carrito.lineas.first.presentacion;
    final partidas = [
      for (var i = 0; i < carrito.lineas.length; i++)
        _Partida(
          id: _nuevoUuid(),
          linea: i + 1,
          renglon: carrito.lineas[i],
        ),
    ];

    final sobre = SobreLocal(
      operacionId: _nuevoUuid(),
      secuencia: _outbox.siguienteSecuencia(),
      visitaId: visita,
      operaciones: [
        OperacionLocal(
          tipo: 'venta.crear',
          entidadId: ventaId,
          datos: {
            'folio_consecutivo': consecutivo,
            'folio_local': folioLocal,
            'cliente_id': clienteId,
            'vendedor_id': _identidad.vendedorId,
            'dispositivo_id': _identidad.dispositivoId,
            'almacen_id': _identidad.almacenId,
            'ruta_id': _identidad.rutaId,
            'carga_id': _identidad.cargaId,
            'tipo': carrito.aCredito ? 'credito' : 'contado',
            'lista_precios_id': primera.listaPreciosId,
            'lista_precios_version': primera.listaPreciosVersion,
            'subtotal': carrito.subtotal.texto,
            'descuento': carrito.descuento.texto,
            'impuestos': carrito.impuestos.texto,
            'total': carrito.total.texto,
            'fecha_dispositivo': momento,
            'fecha_operativa': fechaOperativa,
            'observaciones': observaciones,
            if (ubicacion != null) ...ubicacion.aPayload(),
            'partidas': [for (final p in partidas) p.aPayload()],
          },
        ),
      ],
    );

    _outbox.encolar(
      sobre,
      creadoEn: momento,
      escribirNegocio: (db) {
        db.execute(
          '''
          INSERT INTO ventas (id, folio_consecutivo, folio_local, visita_id,
                              cliente_id, carga_id, tipo, estado,
                              lista_precios_id, lista_precios_version,
                              subtotal, descuento, impuestos, total,
                              lat, lng, ubicacion_precision_m,
                              fecha_dispositivo, fecha_operativa,
                              impreso, reimpresiones, sincronizada, creado_en)
          VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmada', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, 0, 0, 0, ?)
          ''',
          [
            ventaId,
            consecutivo,
            folioLocal,
            visita,
            clienteId,
            _identidad.cargaId,
            carrito.aCredito ? 'credito' : 'contado',
            primera.listaPreciosId,
            primera.listaPreciosVersion,
            _aReal(carrito.subtotal),
            _aReal(carrito.descuento),
            _aReal(carrito.impuestos),
            _aReal(carrito.total),
            ubicacion?.lat,
            ubicacion?.lng,
            ubicacion?.precisionMetros,
            momento,
            fechaOperativa,
            momento,
          ],
        );

        for (final p in partidas) {
          final l = p.renglon;
          db.execute(
            '''
            INSERT INTO venta_partidas (id, venta_id, linea, producto_id,
                                        unidad_codigo, factor_unidad, cantidad,
                                        cantidad_base, precio_unitario, descuento,
                                        tasa_iva, importe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''',
            [
              p.id,
              ventaId,
              p.linea,
              l.presentacion.productoId,
              l.presentacion.unidadCodigo,
              l.presentacion.factor.diezmilesimos / 10000,
              l.cantidad.milesimos / 1000,
              l.enUnidadesBase.milesimos / 1000,
              l.presentacion.precio.diezmilesimos / 10000,
              l.presentacion.tasaIva,
              _aReal(l.importe),
            ],
          );
        }

        // El descuento del camión se agrupa por producto: dos cajas y tres
        // piezas de la misma sopa son dos partidas pero **una sola** existencia.
        // Descontarlas por separado con la guarda `>= cantidad` funcionaría, pero
        // agrupar deja el mensaje de error correcto cuando falta.
        final porProducto = <String, num>{};
        for (final p in partidas) {
          porProducto.update(
            p.renglon.presentacion.productoId,
            (v) => v + p.renglon.enUnidadesBase.milesimos / 1000,
            ifAbsent: () => p.renglon.enUnidadesBase.milesimos / 1000,
          );
        }

        for (final entrada in porProducto.entries) {
          // La guarda va DENTRO del UPDATE: verificar antes y actualizar después
          // dejaría una ventana en medio. Si no afecta filas, la transacción
          // entera se deshace y no queda ni venta ni folio quemado.
          final afectadas = db.select(
            '''
            UPDATE existencias_camion
               SET cant_actual = cant_actual - ?
             WHERE producto_id = ?
               AND cant_actual >= ?
            RETURNING producto_id
            ''',
            [entrada.value, entrada.key, entrada.value],
          );
          if (afectadas.isEmpty) {
            throw VentaRechazada(
              MotivoNoVenta.sinExistencia,
              'ya no hay ${entrada.value} en el camión de ${entrada.key}',
            );
          }
        }

        // La marca del folio, en la misma transacción. Es lo que hace que un
        // rollback no deje hueco en la numeración impresa.
        db.execute(
          'UPDATE folios_rangos SET consumido_hasta = ? WHERE tipo = ?',
          [consecutivo, 'venta'],
        );
      },
    );

    return VentaGuardada(
      id: ventaId,
      folioConsecutivo: consecutivo,
      folioLocal: folioLocal,
      visitaId: visita,
      clienteId: clienteId,
      aCredito: carrito.aCredito,
      subtotal: carrito.subtotal,
      total: carrito.total,
      lineas: carrito.lineas,
      fechaDispositivo: momento,
      fechaOperativa: fechaOperativa,
      ubicacion: ubicacion,
      foliosRestantes: rango.restantes,
    );
  }

  /// Marca la venta como impresa.
  ///
  /// El vendedor toca "Imprimir" **después** de que la venta se guardó (decisión
  /// de negocio, septiembre 2026): si la impresión fuera automática y la
  /// impresora estuviera sin papel o desemparejada, la venta ya estaría escrita y
  /// el vendedor no tendría dónde reintentar sin buscar la venta otra vez.
  ///
  /// La primera impresión marca `impreso`; las siguientes cuentan como
  /// reimpresión, y el ticket se conserva para que salga **idéntico**.
  void marcarImpresa(String ventaId, {required List<int> ticket}) {
    _db.execute('BEGIN IMMEDIATE');
    try {
      final previa = _db.select(
        'SELECT impreso FROM ventas WHERE id = ?',
        [ventaId],
      );
      if (previa.isEmpty) {
        throw ArgumentError('no existe la venta $ventaId');
      }
      final yaImpresa = (previa.single['impreso'] as int) == 1;

      _db.execute(
        yaImpresa
            ? 'UPDATE ventas SET reimpresiones = reimpresiones + 1 WHERE id = ?'
            : 'UPDATE ventas SET impreso = 1, ticket_escpos = ?2 WHERE id = ?1',
        yaImpresa ? [ventaId] : [ventaId, Uint8List.fromList(ticket)],
      );
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }

  /// SQLite guarda dinero como REAL. La conversión cruza el límite del dominio
  /// una sola vez y aquí, igual que al leer (ver `repo_clientes.dart`).
  static num _aReal(Dinero d) => d.centavos / 100;
}

class _Partida {
  const _Partida({
    required this.id,
    required this.linea,
    required this.renglon,
  });

  final String id;
  final int linea;
  final LineaCarrito renglon;

  Map<String, Object?> aPayload() => {
        'id': id,
        'linea': linea,
        'producto_id': renglon.presentacion.productoId,
        'unidad_codigo': renglon.presentacion.unidadCodigo,
        'factor_unidad': renglon.presentacion.factor.texto,
        'cantidad': renglon.cantidad.texto,
        'cantidad_base': renglon.enUnidadesBase.texto,
        'precio_unitario': renglon.presentacion.precio.texto,
        'descuento': Dinero.cero.texto,
        'importe': renglon.importe.texto,
      };
}
