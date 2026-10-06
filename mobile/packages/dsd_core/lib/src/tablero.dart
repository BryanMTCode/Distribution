/// El tablero de Gerencia tal como lo entrega el servidor (Fase 7).
///
/// ─────────────────────────────────────────────────────────────────────────
/// POR QUÉ ESTE MÓDULO VIVE EN dsd_core Y NO EN LA PANTALLA
/// ─────────────────────────────────────────────────────────────────────────
/// Porque lo interesante aquí no es dibujar: es **leer el contrato sin perder
/// centavos**. Los importes llegan como string de dos decimales
/// (contracts/README.md §1.4) y tienen que pasar por `Dinero`, no por
/// `double`. Un `as double` en una tarjeta de tablero no revienta: muestra
/// $42,179.99 donde el servidor dijo $42,180.00, y nadie lo nota hasta que esa
/// cifra se compara con el arqueo de la liquidación.
///
/// Dart puro, sin Flutter: el parseo se prueba en segundos y sin levantar una
/// pantalla.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA ANTIGÜEDAD ES PARTE DEL DATO, NO DE LA PRESENTACIÓN
/// ─────────────────────────────────────────────────────────────────────────
/// Cada bloque trae su `calculadoEn`, y el tablero trae su `Frescura`. En un
/// DSD las cifras del día son un **piso**: un camión sin señal desde las 10 de
/// la mañana tiene ventas reales que no están en ninguna de estas cifras
/// (§0.3). Por eso la frescura es un campo del modelo y no un adorno que la
/// pantalla pueda olvidar: si se perdiera al parsear, la tarjeta se vería
/// igual de bien y mentiría.
library;

import 'dinero.dart';
import 'precio.dart';

/// Lee un importe del contrato. `null` cuando el campo venía nulo.
Dinero? _dineroOpcional(Object? valor) =>
    valor == null ? null : Dinero.deTexto(valor as String);

Dinero _dinero(Object? valor) => Dinero.deTexto(valor! as String);

DateTime? _momentoOpcional(Object? valor) =>
    valor == null ? null : DateTime.parse(valor as String).toLocal();

/// Un porcentaje del contrato: llega como string de un decimal ('50.0').
///
/// Se guarda como `double` y es la única excepción deliberada a "nada de
/// double": un porcentaje no se suma ni se arrastra, se pinta. Lo que no puede
/// ser `double` es el dinero del que se derivó, y ese ya viene aparte.
double _porcentaje(Object? valor) =>
    valor == null ? 0 : double.parse(valor as String);

double? _porcentajeOpcional(Object? valor) =>
    valor == null ? null : double.parse(valor as String);

/// De cuándo son las cifras y qué falta para que estén completas.
class Frescura {
  const Frescura({
    required this.calculadoEn,
    required this.minutos,
    required this.confiable,
    required this.equiposSinSincronizar,
    required this.colaReportada,
    required this.opsEnCuarentena,
    required this.advertencia,
  });

  factory Frescura.deJson(Map<String, Object?> json) => Frescura(
        calculadoEn: _momentoOpcional(json['calculado_en']),
        minutos: json['minutos'] as int?,
        confiable: json['confiable']! as bool,
        equiposSinSincronizar: json['equipos_sin_sincronizar']! as int,
        colaReportada: json['cola_reportada']! as int,
        opsEnCuarentena: json['ops_en_cuarentena']! as int,
        advertencia: json['advertencia'] as String?,
      );

  /// `null` significa que el tablero NUNCA se ha calculado.
  ///
  /// No es lo mismo que cero minutos, y la diferencia importa: cero se lee
  /// como "recién calculado" y esto significa que el worker no está corriendo.
  final DateTime? calculadoEn;
  final int? minutos;
  final bool confiable;
  final int equiposSinSincronizar;
  final int colaReportada;
  final int opsEnCuarentena;
  final String? advertencia;

  bool get nuncaCalculado => calculadoEn == null;
}

/// Qué dice la comparación contra los mismos días de la semana.
enum LecturaDeReferencia {
  /// Más de 10% arriba de su promedio.
  arriba,

  /// Dentro del margen: la venta de un día depende de quién tenía dinero ese
  /// día, y pintar de rojo un 6% abajo enseña a ignorar el rojo.
  parejo,

  /// Más de 10% abajo.
  abajo,

  /// No hay historia bastante para comparar. **No se dibuja flecha.**
  sinReferencia,

  /// Se SABE que a la cifra de hoy le falta información: el teléfono de esa
  /// persona —o, para el total, el de alguna— no ha enviado nada hoy. La
  /// variación es verdad pero es la de un piso, y pintarla de rojo diría «se
  /// cayó» de alguien cuyo día sigue en su bolsillo.
  incompleta;

  static LecturaDeReferencia deTexto(Object? valor) => switch (valor) {
        'arriba' => arriba,
        'parejo' => parejo,
        'abajo' => abajo,
        'incompleta' => incompleta,
        _ => sinReferencia,
      };
}

/// Contra qué se compara el día: el promedio de sus MISMOS DÍAS DE LA SEMANA.
///
/// No contra ayer. La ruta visita a los mismos clientes cada martes, así que
/// comparar el martes contra el lunes mide qué clientes tocaban y no cómo se
/// trabajó. La aritmética la hace el servidor (`domain/tablero.Referencia`), la
/// misma que usa el panel web: si cada pantalla calculara la suya, el gerente
/// vería dos verdades según cuál abriera.
class ReferenciaDelDia {
  const ReferenciaDelDia({
    required this.promedio,
    required this.dias,
    required this.suficiente,
    required this.variacion,
    required this.lectura,
  });

  /// Lo que se lee cuando el servidor no la manda — uno anterior a esta función.
  ///
  /// «Sin referencia» es lo honesto: no hay con qué comparar, así que no hay
  /// flecha. Lo deshonesto sería inventar un cero y pintar al vendedor de verde.
  static const sinDatos = ReferenciaDelDia(
    promedio: Dinero.cero,
    dias: 0,
    suficiente: false,
    variacion: null,
    lectura: LecturaDeReferencia.sinReferencia,
  );

  factory ReferenciaDelDia.deJson(Object? json) {
    if (json is! Map<String, Object?>) return sinDatos;
    return ReferenciaDelDia(
      promedio: _dinero(json['promedio']),
      dias: json['dias']! as int,
      suficiente: json['suficiente']! as bool,
      variacion: _porcentajeOpcional(json['variacion']),
      lectura: LecturaDeReferencia.deTexto(json['lectura']),
    );
  }

  final Dinero promedio;

  /// Cuántos de esos días entraron al promedio. Los que no trabajó no cuentan.
  final int dias;
  final bool suficiente;

  /// Por ciento arriba (positivo) o abajo (negativo). `null` sin referencia.
  final double? variacion;
  final LecturaDeReferencia lectura;
}

class VentaDelDia {
  const VentaDelDia({
    required this.fecha,
    required this.total,
    required this.contado,
    required this.credito,
    required this.documentos,
    required this.ticketPromedio,
    required this.calculadoEn,
    this.referencia = ReferenciaDelDia.sinDatos,
  });

  factory VentaDelDia.deJson(Map<String, Object?> json) => VentaDelDia(
        fecha: DateTime.parse(json['fecha']! as String),
        total: _dinero(json['total']),
        contado: _dinero(json['contado']),
        credito: _dinero(json['credito']),
        documentos: json['documentos']! as int,
        ticketPromedio: _dinero(json['ticket_promedio']),
        calculadoEn: _momentoOpcional(json['calculado_en']),
        referencia: ReferenciaDelDia.deJson(json['referencia']),
      );

  final DateTime fecha;
  final Dinero total;
  final Dinero contado;
  final Dinero credito;
  final int documentos;
  final Dinero ticketPromedio;
  final DateTime? calculadoEn;

  /// El promedio de los mismos días de la semana anteriores.
  final ReferenciaDelDia referencia;
}

class VisitasDelDia {
  const VisitasDelDia({
    required this.visitas,
    required this.conVenta,
    required this.noDrops,
    required this.noDropsNuestros,
    required this.efectividad,
    required this.dropSize,
    required this.calculadoEn,
  });

  factory VisitasDelDia.deJson(Map<String, Object?> json) => VisitasDelDia(
        visitas: json['visitas']! as int,
        conVenta: json['con_venta']! as int,
        noDrops: json['no_drops']! as int,
        noDropsNuestros: json['no_drops_nuestros']! as int,
        efectividad: _porcentaje(json['efectividad']),
        dropSize: _dinero(json['drop_size']),
        calculadoEn: _momentoOpcional(json['calculado_en']),
      );

  final int visitas;
  final int conVenta;
  final int noDrops;

  /// Los no-drops que la empresa puede arreglar: categorías 'operacion',
  /// 'producto' y 'vendedor'. Es la cifra accionable: ocho visitas perdidas
  /// por "no traigo lo que pidió" no son un problema de ventas, son de carga.
  final int noDropsNuestros;
  final double efectividad;
  final Dinero dropSize;
  final DateTime? calculadoEn;

  int get perdidas => visitas - conVenta;
}

class CobranzaDelDia {
  const CobranzaDelDia({
    required this.cobradoHoy,
    required this.cobradoEfectivo,
    required this.saldoTotal,
    required this.saldoVencido,
    required this.facturasVencidas,
    required this.clientesVencidos,
    required this.calculadoEn,
  });

  factory CobranzaDelDia.deJson(Map<String, Object?> json) => CobranzaDelDia(
        cobradoHoy: _dinero(json['cobrado_hoy']),
        cobradoEfectivo: _dinero(json['cobrado_efectivo']),
        saldoTotal: _dinero(json['saldo_total']),
        saldoVencido: _dinero(json['saldo_vencido']),
        facturasVencidas: json['facturas_vencidas']! as int,
        clientesVencidos: json['clientes_vencidos']! as int,
        calculadoEn: _momentoOpcional(json['calculado_en']),
      );

  final Dinero cobradoHoy;

  /// El efectivo va aparte porque es el único que entra al arqueo de la
  /// liquidación: una transferencia no está en la bolsa de nadie.
  final Dinero cobradoEfectivo;
  final Dinero saldoTotal;
  final Dinero saldoVencido;
  final int facturasVencidas;
  final int clientesVencidos;

  /// La cartera es un SALDO, no un flujo: su antigüedad es la del cálculo y no
  /// la del día operativo que se está viendo.
  final DateTime? calculadoEn;
}

class MermasDelDia {
  const MermasDelDia({
    required this.documentos,
    required this.unidades,
    required this.calculadoEn,
  });

  factory MermasDelDia.deJson(Map<String, Object?> json) => MermasDelDia(
        documentos: json['documentos']! as int,
        unidades: Cantidad.deTexto(json['unidades']! as String),
        calculadoEn: _momentoOpcional(json['calculado_en']),
      );

  final int documentos;
  final Cantidad unidades;
  final DateTime? calculadoEn;
}

class RenglonVendedor {
  const RenglonVendedor({
    required this.vendedorId,
    required this.codigo,
    required this.nombre,
    required this.venta,
    required this.documentos,
    required this.visitas,
    required this.conVenta,
    required this.noDrops,
    required this.cobrado,
    required this.efectividad,
    this.referencia = ReferenciaDelDia.sinDatos,
    this.ultimoPush,
    this.colaReportada = 0,
    this.sinSincronizar = false,
  });

  factory RenglonVendedor.deJson(Map<String, Object?> json) => RenglonVendedor(
        vendedorId: json['vendedor_id']! as String,
        codigo: json['codigo'] as String?,
        nombre: json['nombre']! as String,
        venta: _dinero(json['venta']),
        documentos: json['documentos']! as int,
        visitas: json['visitas']! as int,
        conVenta: json['con_venta']! as int,
        noDrops: json['no_drops']! as int,
        cobrado: _dinero(json['cobrado']),
        efectividad: _porcentaje(json['efectividad']),
        referencia: ReferenciaDelDia.deJson(json['referencia']),
        ultimoPush: _momentoOpcional(json['ultimo_push']),
        colaReportada: (json['cola_reportada'] as int?) ?? 0,
        // Un servidor anterior no lo manda. `false` es lo que la app ya mostraba
        // antes de saberlo, así que no empeora nada: solo deja de mejorar.
        sinSincronizar: (json['sin_sincronizar'] as bool?) ?? false,
      );

  final String vendedorId;
  final String? codigo;
  final String nombre;
  final Dinero venta;
  final int documentos;
  final int visitas;
  final int conVenta;
  final int noDrops;
  final Dinero cobrado;
  final double efectividad;

  /// Su propio mismo día de la semana.
  final ReferenciaDelDia referencia;

  /// El envío más reciente de cualquiera de sus teléfonos activos.
  final DateTime? ultimoPush;

  /// Lo que sus teléfonos dijeron tener pendiente hoy.
  final int colaReportada;

  /// Su teléfono no ha enviado nada hoy: **su cero no es un cero**.
  ///
  /// Solo puede ser verdad del día de hoy; para un día cerrado el servidor lo
  /// manda en `false`, porque «no ha enviado hoy» no dice nada del martes pasado.
  final bool sinSincronizar;

  /// Un vendedor que no ha hecho nada. Es el renglón más urgente del tablero:
  /// a media mañana significa que algo pasó con el camión o con el teléfono.
  ///
  /// Ojo: un vendedor `sinSincronizar` también se ve sin actividad, y NO es lo
  /// mismo. La pantalla pregunta primero por la sincronía.
  bool get sinActividad => visitas == 0 && cobrado.esCero;
}

/// Cómo va una ruta contra su objetivo del mes.
class RenglonRuta {
  const RenglonRuta({
    required this.rutaId,
    required this.codigo,
    required this.nombre,
    required this.ventaMes,
    required this.objetivo,
    required this.logrado,
    required this.esperado,
    required this.diferencia,
    required this.semaforo,
    required this.visitasMes,
    required this.diasConVenta,
    required this.clientesDistintos,
  });

  factory RenglonRuta.deJson(Map<String, Object?> json) => RenglonRuta(
        rutaId: json['ruta_id']! as String,
        codigo: json['codigo']! as String,
        nombre: json['nombre']! as String,
        ventaMes: _dinero(json['venta_mes']),
        objetivo: _dineroOpcional(json['objetivo']),
        logrado: _porcentajeOpcional(json['logrado']),
        esperado: _porcentaje(json['esperado']),
        diferencia: _porcentajeOpcional(json['diferencia']),
        semaforo: json['semaforo']! as String,
        visitasMes: json['visitas_mes']! as int,
        diasConVenta: json['dias_con_venta']! as int,
        clientesDistintos: json['clientes_distintos']! as int,
      );

  final String rutaId;
  final String codigo;
  final String nombre;
  final Dinero ventaMes;

  /// `null` cuando nadie le fijó meta. La pantalla lo dice con esas palabras en
  /// vez de pintar una barra vacía: una barra contra un objetivo que no existe
  /// sería una cifra sin dueño.
  final Dinero? objetivo;
  final double? logrado;

  /// Lo que se esperaría a prorrata de los días transcurridos. Sin esto, "67%"
  /// se lee igual el día 10 que el día 28, y es excelente o grave según cuál.
  final double esperado;
  final double? diferencia;

  /// 'sin_objetivo' | 'adelante' | 'cerca' | 'atras'
  final String semaforo;
  final int visitasMes;
  final int diasConVenta;
  final int clientesDistintos;

  bool get sinObjetivo => objetivo == null;
}

class AvanceDelMes {
  const AvanceDelMes({
    required this.periodo,
    required this.diaDelMes,
    required this.diasDelMes,
    required this.rutas,
    required this.ventaSinRuta,
    required this.documentosSinRuta,
  });

  factory AvanceDelMes.deJson(Map<String, Object?> json) => AvanceDelMes(
        periodo: DateTime.parse(json['periodo']! as String),
        diaDelMes: json['dia_del_mes']! as int,
        diasDelMes: json['dias_del_mes']! as int,
        rutas: ((json['rutas'] ?? const <Object?>[]) as List)
            .cast<Map<String, Object?>>()
            .map(RenglonRuta.deJson)
            .toList(),
        ventaSinRuta: _dinero(json['venta_sin_ruta']),
        documentosSinRuta: json['documentos_sin_ruta']! as int,
      );

  final DateTime periodo;
  final int diaDelMes;
  final int diasDelMes;
  final List<RenglonRuta> rutas;

  /// Venta del mes que no se pudo atribuir a ninguna ruta. Se muestra porque
  /// es la diferencia entre el total y la suma de las barras.
  final Dinero ventaSinRuta;
  final int documentosSinRuta;

  bool get hayVentaSinRuta => documentosSinRuta > 0;
}

class Tablero {
  const Tablero({
    required this.frescura,
    required this.venta,
    required this.visitas,
    required this.cobranza,
    required this.mermas,
    required this.vendedores,
    required this.avance,
  });

  factory Tablero.deJson(Map<String, Object?> json) => Tablero(
        frescura: Frescura.deJson(json['frescura']! as Map<String, Object?>),
        venta: VentaDelDia.deJson(json['venta']! as Map<String, Object?>),
        visitas: VisitasDelDia.deJson(json['visitas']! as Map<String, Object?>),
        cobranza:
            CobranzaDelDia.deJson(json['cobranza']! as Map<String, Object?>),
        mermas: MermasDelDia.deJson(json['mermas']! as Map<String, Object?>),
        vendedores: ((json['vendedores'] ?? const <Object?>[]) as List)
            .cast<Map<String, Object?>>()
            .map(RenglonVendedor.deJson)
            .toList(),
        avance: AvanceDelMes.deJson(json['avance']! as Map<String, Object?>),
      );

  final Frescura frescura;
  final VentaDelDia venta;
  final VisitasDelDia visitas;
  final CobranzaDelDia cobranza;
  final MermasDelDia mermas;
  final List<RenglonVendedor> vendedores;
  final AvanceDelMes avance;
}

/// Un punto del mapa del día: una venta o una visita perdida.
class PuntoDelMapa {
  const PuntoDelMapa({
    required this.clase,
    required this.lat,
    required this.lng,
    required this.cliente,
    required this.vendedor,
    required this.importe,
    required this.motivo,
    required this.momento,
  });

  factory PuntoDelMapa.deJson(Map<String, Object?> json) => PuntoDelMapa(
        clase: json['clase']! as String,
        // Las coordenadas sí son double: una latitud no se suma ni se arrastra,
        // y el servidor las manda con siete decimales, que `double` representa
        // de sobra para ~1 cm.
        lat: double.parse(json['lat']!.toString()),
        lng: double.parse(json['lng']!.toString()),
        cliente: json['cliente']! as String,
        vendedor: json['vendedor'] as String?,
        importe: _dineroOpcional(json['importe']),
        motivo: json['motivo'] as String?,
        momento: DateTime.parse(json['momento']! as String).toLocal(),
      );

  /// 'venta' | 'no_drop'
  final String clase;
  final double lat;
  final double lng;
  final String cliente;
  final String? vendedor;
  final Dinero? importe;
  final String? motivo;
  final DateTime momento;

  bool get esVenta => clase == 'venta';
}

class MapaDelDia {
  const MapaDelDia({
    required this.fecha,
    required this.puntos,
    required this.recortados,
    required this.calculadoEn,
  });

  factory MapaDelDia.deJson(Map<String, Object?> json) => MapaDelDia(
        fecha: DateTime.parse(json['fecha']! as String),
        puntos: ((json['puntos'] ?? const <Object?>[]) as List)
            .cast<Map<String, Object?>>()
            .map(PuntoDelMapa.deJson)
            .toList(),
        recortados: json['recortados']! as bool,
        calculadoEn: _momentoOpcional(json['calculado_en']),
      );

  final DateTime fecha;
  final List<PuntoDelMapa> puntos;

  /// Si el servidor dejó puntos fuera por el tope. Un mapa recortado en
  /// silencio haría que alguien contara visitas sobre el dibujo y le faltaran.
  final bool recortados;
  final DateTime? calculadoEn;

  int get ventas => puntos.where((p) => p.esVenta).length;
  int get perdidas => puntos.length - ventas;
}
