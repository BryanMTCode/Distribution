/// El tablero de Gerencia (Fase 7).
///
/// ─────────────────────────────────────────────────────────────────────────
/// QUÉ CONTESTA, Y POR QUÉ ESTAS SEIS CIFRAS
/// ─────────────────────────────────────────────────────────────────────────
/// La pregunta del gerente a media mañana no es "cuánto vendimos el mes
/// pasado" —eso es el laboratorio— sino **cómo va el día y qué hay que mover
/// hoy**:
///
///   · Venta del día, con su desglose efectivo/transferencia (todo es de
///     contado, ADR 0002 §81).
///   · Avance del mes por ruta contra su objetivo.
///   · Transferencias que todavía no se ven en el banco.
///   · Efectividad de visita y cuántos no-drops son NUESTROS.
///   · Quién va bien y quién no ha hecho nada todavía.
///   · El mapa del día.
///
/// ─────────────────────────────────────────────────────────────────────────
/// LA ANTIGÜEDAD ESTÁ ARRIBA Y NO SE PUEDE PASAR POR ALTO
/// ─────────────────────────────────────────────────────────────────────────
/// Es la decisión de diseño que gobierna toda la pantalla. "Tiempo real" en un
/// DSD es **"tiempo real de lo que ha sincronizado"** (§0.3): un camión sin
/// señal desde las 10 tiene ventas reales que no están en ninguna de estas
/// cifras. Así que la marca de frescura va primero, antes de la primera cifra,
/// y dice las dos cosas que importan — cuándo se calculó y qué faltaba en ese
/// momento.
///
/// Un tablero que presume de instantáneo es un tablero que miente una vez al
/// día, el día que un teléfono se queda sin batería.
library;

import 'package:dsd_core/dsd_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../estado/sesion.dart';
import '../../datos/repo_tablero.dart';
import '../../estado/tablero.dart';
import 'comunes.dart';
import 'periodo.dart';
import 'mapa.dart';

class PantallaGerencia extends ConsumerStatefulWidget {
  const PantallaGerencia({super.key, required this.nombre});

  final String nombre;

  @override
  ConsumerState<PantallaGerencia> createState() => _EstadoGerencia();
}

class _EstadoGerencia extends ConsumerState<PantallaGerencia> {
  /// Día y periodo en UNA pantalla (pedido en operación, octubre 2026: «¿las
  /// barras de día y periodo no se pueden combinar?»). Un solo día muestra el
  /// tablero completo —avance del mes, cartera, por vendedor, mapa—; varios
  /// días muestran el resumen del periodo, por vendedor y por día.
  PeriodoElegido _periodo = const PeriodoElegido('hoy');

  /// Cambia para que el resumen del periodo vuelva a consultar al refrescar.
  int _recarga = 0;

  @override
  void initState() {
    super.initState();
    // La carga se dispara aquí y no en el `build` del Notifier: un Notifier que
    // lanza trabajo al construirse es imposible de probar sin pelear con el
    // reloj, y además se volvería a disparar en cada reconstrucción.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(tableroProvider.notifier).cargar();
    });
  }

  String get _hoy => diaOperativoDe(ref.read(relojProvider)());

  /// El día, si el periodo elegido es de un solo día; si no, nulo.
  String? _diaUnico(PeriodoElegido p) {
    final hoy = _hoy;
    return switch (p.clave) {
      'hoy' => hoy,
      'ayer' => diaOperativoDe(DateTime.parse(hoy).subtract(const Duration(hours: 12))),
      'rango' when p.desde == p.hasta => p.desde,
      _ => null,
    };
  }

  void _elegir(PeriodoElegido p) {
    setState(() => _periodo = p);
    final dia = _diaUnico(p);
    if (dia == null) return;
    ref.read(fechaDelTableroProvider.notifier).state =
        dia == _hoy ? null : DateTime.parse(dia);
    ref.read(tableroProvider.notifier).cargar();
  }

  Future<void> _refrescar() async {
    if (_diaUnico(_periodo) != null) {
      await ref.read(tableroProvider.notifier).cargar();
    } else {
      setState(() => _recarga++);
    }
  }

  @override
  Widget build(BuildContext context) {
    final dia = _diaUnico(_periodo);

    return Scaffold(
      // La clave va en el Scaffold y no en la lista de cifras: así identifica
      // "el portal llevó a la pantalla de gerencia", que es verdad también
      // cuando todavía no hay cifras que mostrar. Las cifras tienen la suya.
      key: const Key('panel_gerencia'),
      appBar: AppBar(
        // El nombre a la vista, y no por cortesía: el tablero muestra la venta
        // de todas las rutas y la cartera completa, así que quien lo tenga
        // abierto debe poder comprobar de un vistazo con qué usuario entró.
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Tablero'),
            Text(
              widget.nombre,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: Theme.of(context).appBarTheme.foregroundColor,
                  ),
            ),
          ],
        ),
        actions: [
          IconButton(
            key: const Key('boton_refrescar_tablero'),
            tooltip: 'Volver a consultar',
            icon: const Icon(Icons.refresh),
            onPressed: _refrescar,
          ),
          IconButton(
            key: const Key('boton_salir_gerencia'),
            tooltip: 'Salir',
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(sesionProvider.notifier).salir(),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: SelectorDePeriodo(
              periodos: periodosDeSiempre,
              elegido: _periodo,
              hoy: ref.watch(relojProvider)(),
              conUnDia: true,
              onElegir: _elegir,
            ),
          ),
          Expanded(
            child: dia != null
                ? _vistaDelDia()
                : VistaDelPeriodo(
                    key: ValueKey('periodo-${_periodo.clave}-${_periodo.desde}-'
                        '${_periodo.hasta}-$_recarga'),
                    periodo: _periodo,
                    onVerDia: (fecha) => _elegir(PeriodoElegido.rango(fecha, fecha)),
                  ),
          ),
        ],
      ),
    );
  }

  Widget _vistaDelDia() {
    final estado = ref.watch(tableroProvider);
    return RefreshIndicator(
        onRefresh: () => ref.read(tableroProvider.notifier).cargar(),
        child: switch (estado) {
          TableroCargando() => const Center(
              key: Key('tablero_cargando'),
              child: CircularProgressIndicator(),
            ),
          TableroListo(:final local) =>
            _Cuerpo(local: local, deLaCopia: false, hoy: ref.watch(relojProvider)()),
          TableroDeLaCopia(:final local) =>
            _Cuerpo(local: local, deLaCopia: true, hoy: ref.watch(relojProvider)()),
          TableroSinNada(:final motivo) => _SinCifras(
              clave: 'tablero_sin_nada',
              icono: Icons.cloud_off_outlined,
              titulo: 'Todavía no se ha podido bajar el tablero',
              detalle: 'El tablero necesita señal para consultar al servidor, '
                  'y este teléfono no tiene una copia guardada de este día.\n\n'
                  '($motivo)',
              onReintentar: () => ref.read(tableroProvider.notifier).cargar(),
            ),
          TableroSinPermiso() => const _SinCifras(
              clave: 'tablero_sin_permiso',
              icono: Icons.lock_outline,
              titulo: 'Tu usuario no puede ver el tablero',
              // Sin botón de reintentar: no se arregla reintentando, y
              // ofrecerlo invitaría a perder el tiempo picándole.
              detalle: 'Hace falta el permiso "tablero.ver". Se da desde el '
                  'panel de la oficina.',
            ),
          TableroSinSesionEnLinea() => const _SinCifras(
              clave: 'tablero_sin_sesion_en_linea',
              icono: Icons.wifi_off_outlined,
              titulo: 'Este equipo no tiene sesión en línea',
              // Sin botón de reintentar: no hay nada que reintentar hasta que
              // alguien entre con código y contraseña.
              detalle: 'El tablero consulta al servidor en vivo, y para eso '
                  'hace falta entrar con señal usando código y contraseña. '
                  'Sal y vuelve a entrar con el botón de Gerencia.',
            ),
          TableroSesionVencida() => _SinCifras(
              clave: 'tablero_sesion_vencida',
              icono: Icons.lock_clock_outlined,
              titulo: 'La sesión venció',
              detalle: 'Vuelve a entrar para seguir viendo el tablero.',
              onSalir: () => ref.read(sesionProvider.notifier).salir(),
            ),
          TableroConError(:final mensaje) => _SinCifras(
              clave: 'tablero_con_error',
              icono: Icons.error_outline,
              titulo: 'El servidor no pudo contestar',
              detalle: mensaje,
              onReintentar: () => ref.read(tableroProvider.notifier).cargar(),
            ),
        },
    );
  }
}

class _Cuerpo extends StatelessWidget {
  const _Cuerpo({required this.local, required this.deLaCopia, required this.hoy});

  final TableroLocal local;
  final bool deLaCopia;
  final DateTime hoy;

  @override
  Widget build(BuildContext context) {
    final t = local.tablero;
    return ListView(
      key: const Key('tablero_cifras'),
      padding: const EdgeInsets.all(16),
      children: [
        // La marca va PRIMERO, antes de cualquier cifra. Debajo de las tarjetas
        // se leería como una nota al pie de algo que ya se dio por cierto.
        MarcaDeFrescura(
          frescura: t.frescura,
          recibidoEn: local.recibidoEn,
          deLaCopia: deLaCopia,
        ),

        // El día de las cifras, con su nombre: «Hoy» a secas no dice si se está
        // viendo hoy o un día que se eligió en el calendario.
        _Titulo(
          encabezadoDelDia(diaOperativoDe(t.venta.fecha), hoy: diaOperativoDe(hoy)),
          clave: const Key('dia_del_tablero'),
        ),
        _Rejilla(
          children: [
            Tarjeta(
              cifra: pesos(t.venta.total, conCentavos: false),
              etiqueta: 'vendido hoy',
              // La referencia va en la tarjeta y no en un renglón aparte: la
              // cifra sola no dice si es buena. Contra los MISMOS días de la
              // semana, porque la ruta visita a los mismos clientes cada martes.
              detalle: [
                textoDeReferencia(t.venta.referencia, t.venta.fecha) ??
                    'Sin referencia todavía: hacen falta dos '
                        '${mismosDias(t.venta.fecha)} con operación',
                '${t.venta.documentos} remisiones · ticket promedio '
                    '${pesos(t.venta.ticketPromedio)}',
              ].join('\n'),
              alerta: t.venta.referencia.lectura == LecturaDeReferencia.abajo,
            ),
            Tarjeta(
              cifra: pesos(t.venta.efectivo, conCentavos: false),
              etiqueta: 'en efectivo',
              // El efectivo se separa porque es el único que entra al corte:
              // una transferencia no está en la bolsa del vendedor.
              detalle: '${pesos(t.venta.transferencia)} por transferencia, '
                  'que no entra al corte',
            ),
            Tarjeta(
              cifra: '${t.visitas.efectividad.toStringAsFixed(1)}%',
              etiqueta: 'efectividad de visita',
              detalle: '${t.visitas.conVenta} de ${t.visitas.visitas} visitas '
                  'terminaron en venta',
            ),
            Tarjeta(
              cifra: '${t.visitas.noDropsNuestros}',
              etiqueta: 'visitas perdidas que son nuestras',
              // La cifra accionable del tablero: ocho no-drops por "no traigo
              // lo que pidió" no son un problema de ventas, son de carga, y se
              // arreglan en la bodega mañana.
              detalle: 'De ${t.visitas.noDrops} no-drops. Estas se arreglan '
                  'desde la bodega o la oficina.',
              alerta: t.visitas.noDropsNuestros > 0,
            ),
          ],
        ),

        if (t.porConfirmar.cuantas > 0) ...[
          const SizedBox(height: 10),
          // Un pendiente de cualquier día, no un flujo de hoy: lo que la
          // oficina tiene que ir a buscar al banco para cuadrar el dinero.
          Text(
            '${t.porConfirmar.cuantas} transferencias por '
            '${pesos(t.porConfirmar.importe)} esperan confirmarse en el banco.',
            key: const Key('transferencias_por_confirmar'),
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],

        _Titulo('Avance del mes'),
        _Avance(avance: t.avance),

        _Titulo('Por vendedor'),
        if (t.vendedores.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text('Todavía no hay movimiento de ningún vendedor hoy.'),
          )
        else
          ...t.vendedores.map(
            (v) => _RenglonVendedorWidget(renglon: v, fecha: t.venta.fecha),
          ),

        if (t.mermas.documentos > 0) ...[
          _Titulo('Mermas'),
          Text(
            '${t.mermas.documentos} documento(s) por '
            '${t.mermas.unidades.texto} unidades. '
            'Las devoluciones de cliente NO están aquí: esa mercancía vuelve '
            'buena al camión y se puede revender.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],

        const SizedBox(height: 20),
        OutlinedButton.icon(
          key: const Key('boton_mapa'),
          onPressed: () => Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => const PantallaMapa()),
          ),
          icon: const Icon(Icons.place_outlined),
          label: const Text('Ver el mapa del día'),
        ),
        const SizedBox(height: 32),
      ],
    );
  }
}

class _Titulo extends StatelessWidget {
  const _Titulo(this.texto, {this.clave});

  final String texto;
  final Key? clave;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 22, bottom: 10),
        child: Text(
          texto,
          key: clave,
          style: Theme.of(context)
              .textTheme
              .titleMedium
              ?.copyWith(fontWeight: FontWeight.w600),
        ),
      );
}

/// Dos columnas en un teléfono, cuatro en una tablet.
///
/// Filas de `IntrinsicHeight` y no un `GridView.count`, y la razón es un
/// defecto que apareció en la primera prueba: un grid exige una relación de
/// aspecto FIJA, y el detalle de cada tarjeta mide lo que mide su texto —tres
/// renglones en una, cinco en otra, y más en un teléfono angosto o con el
/// tamaño de letra del sistema subido—. Con relación fija, la tarjeta más
/// larga se desborda y Flutter pinta la franja amarilla y negra encima de la
/// cifra.
///
/// `IntrinsicHeight` deja que la fila mida lo que necesita la tarjeta más alta,
/// y `Expanded` iguala el ancho. Nunca se desborda y las dos quedan parejas.
class _Rejilla extends StatelessWidget {
  const _Rejilla({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (_, limites) {
          final columnas = limites.maxWidth > 560 ? 4 : 2;
          final filas = <Widget>[];
          for (var i = 0; i < children.length; i += columnas) {
            final grupo = children.sublist(
              i,
              (i + columnas).clamp(0, children.length),
            );
            filas.add(
              IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (var c = 0; c < columnas; c++) ...[
                      if (c > 0) const SizedBox(width: 12),
                      // Los huecos de la última fila se rellenan con espacio
                      // vacío: sin esto, una fila de una sola tarjeta la
                      // estiraría a todo el ancho y rompería la cuadrícula.
                      Expanded(
                        child: c < grupo.length ? grupo[c] : const SizedBox(),
                      ),
                    ],
                  ],
                ),
              ),
            );
            if (i + columnas < children.length) {
              filas.add(const SizedBox(height: 12));
            }
          }
          return Column(children: filas);
        },
      );
}

class _Avance extends StatelessWidget {
  const _Avance({required this.avance});

  final AvanceDelMes avance;

  @override
  Widget build(BuildContext context) {
    final estilo = Theme.of(context).textTheme.bodySmall;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Día ${avance.diaDelMes} de ${avance.diasDelMes}. '
          'La marca negra de cada barra es el avance esperado a prorrata de los '
          'días transcurridos — son días naturales, así que en domingo todas se '
          'ven un poco atrás.',
          style: estilo,
        ),
        const SizedBox(height: 14),
        if (avance.rutas.isEmpty)
          const Text('No hay rutas activas.')
        else
          ...avance.rutas.map((r) => _RenglonRutaWidget(renglon: r)),
        if (avance.hayVentaSinRuta) ...[
          const SizedBox(height: 10),
          Text(
            // Se muestra porque es la diferencia entre el total del mes y la
            // suma de las barras. Callarlo haría que las cifras no cuadraran
            // sin explicación, y un total que no cuadra con su desglose
            // destruye la confianza en todo lo demás.
            '${pesos(avance.ventaSinRuta)} del mes '
            '(${avance.documentosSinRuta} remisiones) no quedó asignado a '
            'ninguna ruta, así que no está en ninguna barra de arriba.',
            style: estilo,
          ),
        ],
      ],
    );
  }
}

class _RenglonRutaWidget extends StatelessWidget {
  const _RenglonRutaWidget({required this.renglon});

  final RenglonRuta renglon;

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    return Padding(
      key: Key('ruta_${renglon.codigo}'),
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '${renglon.codigo} · ${renglon.nombre}',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              Text(pesos(renglon.ventaMes, conCentavos: false)),
            ],
          ),
          const SizedBox(height: 6),
          if (renglon.sinObjetivo)
            Text(
              // No se inventa una barra: una barra contra un objetivo que nadie
              // fijó sería una cifra sin dueño. Y la ruta aparece igual, porque
              // la que no tiene meta es justo la que hay que notar.
              'Sin objetivo este mes. Fíjalo en el panel para poder comparar.',
              style: TextStyle(fontSize: 12, color: esquema.onSurfaceVariant),
            )
          else ...[
            BarraDeAvance(
              logrado: renglon.logrado!,
              esperado: renglon.esperado,
              semaforo: renglon.semaforo,
            ),
            const SizedBox(height: 4),
            Text(
              '${renglon.logrado!.toStringAsFixed(1)}% de '
              '${pesos(renglon.objetivo!, conCentavos: false)} · '
              'esperado ${renglon.esperado.toStringAsFixed(1)}%',
              style: TextStyle(fontSize: 12, color: esquema.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
  }
}

class _RenglonVendedorWidget extends StatelessWidget {
  const _RenglonVendedorWidget({required this.renglon, required this.fecha});

  final RenglonVendedor renglon;
  final DateTime fecha;

  /// Lo que se dice de la persona, en el orden en que importa.
  ///
  /// La sincronía va PRIMERO, antes que la actividad. Un vendedor cuyo teléfono
  /// no ha enviado nada se ve en cifras igual que uno que no ha vendido —todo en
  /// cero— y antes de esto la app decía «sin movimiento» en los dos casos. En el
  /// primero era falso: no es que no haya vendido, es que no sabemos. Piden dos
  /// llamadas distintas.
  String _subtitulo() {
    if (renglon.sinSincronizar) {
      final partes = [
        'Su teléfono no ha enviado nada hoy',
        renglon.ultimoPush == null
            ? 'nunca ha sincronizado'
            : 'último envío ${antiguedadEnPalabras(renglon.ultimoPush)}',
        if (renglon.colaReportada > 0) '${renglon.colaReportada} en cola',
      ];
      return partes.join(' · ');
    }
    if (renglon.sinActividad) {
      // Es el renglón más urgente del tablero: a media mañana significa que
      // algo pasó con el camión, y es justo el vendedor que un orden por venta
      // pondría al final y nadie vería.
      return 'Sincronizó y no trae movimiento todavía hoy';
    }
    return [
      '${renglon.visitas} visitas · ${renglon.conVenta} con venta · '
          '${renglon.efectividad.toStringAsFixed(0)}% · '
          '${pesos(renglon.efectivo, conCentavos: false)} en efectivo',
      ?textoDeReferencia(renglon.referencia, fecha),
    ].join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    final esquema = Theme.of(context).colorScheme;
    final urgente = renglon.sinSincronizar || renglon.sinActividad;
    return ListTile(
      key: Key('vendedor_${renglon.codigo ?? renglon.vendedorId}'),
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        renglon.sinSincronizar
            ? Icons.sync_problem
            : renglon.sinActividad
                ? Icons.error_outline
                : Icons.person_outline,
        color: urgente ? esquema.error : null,
      ),
      title: Text(renglon.nombre),
      subtitle: Text(
        _subtitulo(),
        style: renglon.referencia.lectura == LecturaDeReferencia.abajo &&
                !urgente
            ? TextStyle(color: esquema.error)
            : null,
      ),
      trailing: Text(
        pesos(renglon.venta, conCentavos: false),
        style: const TextStyle(fontWeight: FontWeight.w600),
      ),
    );
  }
}

/// Lo que se muestra cuando no hay cifras. Cada caso dice qué hacer.
class _SinCifras extends StatelessWidget {
  const _SinCifras({
    required this.clave,
    required this.icono,
    required this.titulo,
    required this.detalle,
    this.onReintentar,
    this.onSalir,
  });

  final String clave;
  final IconData icono;
  final String titulo;
  final String detalle;
  final VoidCallback? onReintentar;
  final VoidCallback? onSalir;

  @override
  Widget build(BuildContext context) => ListView(
        key: Key(clave),
        padding: const EdgeInsets.all(32),
        children: [
          const SizedBox(height: 48),
          Icon(icono, size: 56),
          const SizedBox(height: 16),
          Text(
            titulo,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 10),
          Text(detalle, textAlign: TextAlign.center),
          if (onReintentar != null) ...[
            const SizedBox(height: 24),
            FilledButton.icon(
              key: const Key('boton_reintentar_tablero'),
              onPressed: onReintentar,
              icon: const Icon(Icons.refresh),
              label: const Text('Volver a intentar'),
            ),
          ],
          if (onSalir != null) ...[
            const SizedBox(height: 24),
            FilledButton.icon(
              key: const Key('boton_volver_a_entrar'),
              onPressed: onSalir,
              icon: const Icon(Icons.login),
              label: const Text('Volver a entrar'),
            ),
          ],
        ],
      );
}
