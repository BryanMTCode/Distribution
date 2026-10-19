package com.distribuidora.dsd_app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothClass
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.IOException
import java.util.UUID
import java.util.concurrent.Executors

/**
 * La impresora térmica de verdad, por Bluetooth (ADR 0002 §139).
 *
 * Las térmicas portátiles de 58 mm —la EC Line EC-MP200 entre ellas— hablan el
 * perfil serial de Bluetooth (SPP): se abre un socket RFCOMM con la impresora ya
 * emparejada en los Ajustes del teléfono, se le mandan los bytes ESC/POS que arma
 * Dart y se cierra. Aquí no se arma nada del ticket: eso vive en `dsd_core` y
 * está probado byte por byte.
 *
 * Solo impresoras YA emparejadas: buscar equipos nuevos pediría además permiso
 * de ubicación y de escaneo, y emparejar en los Ajustes de Android es un paso que
 * se hace una vez.
 *
 * Nada de esto lanza hacia Dart: cada falla vuelve como un código («no_conecta»,
 * «bluetooth_apagado», «sin_permiso»…) que la app traduce a lo que el vendedor
 * tiene que hacer.
 */
class ImpresoraBluetooth(private val actividad: Activity, mensajero: BinaryMessenger) {
    companion object {
        const val CODIGO_PERMISO = 4207

        // El Serial Port Profile.
        private val SPP: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")

        // De a poco: el búfer de las térmicas baratas es chico.
        private const val TROZO = 256
    }

    private val hilo = Executors.newSingleThreadExecutor()
    private val principal = Handler(Looper.getMainLooper())

    // Lo que espera a que el vendedor conteste el permiso de Bluetooth.
    private var alConceder: (() -> Unit)? = null
    private var alNegar: (() -> Unit)? = null

    init {
        MethodChannel(mensajero, "dsd/impresora").setMethodCallHandler { llamada, resultado ->
            atender(llamada, resultado)
        }
    }

    private fun atender(llamada: MethodCall, resultado: MethodChannel.Result) {
        when (llamada.method) {
            "emparejadas" -> conPermiso(resultado) { resultado.success(emparejadas()) }
            "imprimir" -> {
                val direccion = llamada.argument<String>("direccion")
                val bytes = llamada.argument<ByteArray>("bytes")
                if (direccion == null || bytes == null) {
                    resultado.success(falla("datos", "faltan la impresora o el ticket"))
                    return
                }
                conPermiso(resultado) { imprimir(direccion, bytes, resultado) }
            }
            else -> resultado.notImplemented()
        }
    }

    // -------------------------------------------------------------------------
    // El permiso (Android 12 en adelante)
    // -------------------------------------------------------------------------

    private fun conPermiso(resultado: MethodChannel.Result, accion: () -> Unit) {
        // Antes de Android 12 el permiso se concede al instalar (BLUETOOTH).
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            accion()
            return
        }
        if (actividad.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            accion()
            return
        }
        if (alConceder != null) {
            resultado.success(falla("ocupada", "ya se está pidiendo el permiso de Bluetooth"))
            return
        }
        alConceder = accion
        alNegar = {
            resultado.success(
                falla("sin_permiso", "la app no tiene permiso de usar el Bluetooth")
            )
        }
        actividad.requestPermissions(
            arrayOf(Manifest.permission.BLUETOOTH_CONNECT), CODIGO_PERMISO
        )
    }

    /** Lo llama `MainActivity` cuando el vendedor contesta el diálogo. */
    fun alContestarPermiso(codigo: Int, concedidos: IntArray): Boolean {
        if (codigo != CODIGO_PERMISO) return false
        val conceder = alConceder
        val negar = alNegar
        alConceder = null
        alNegar = null
        if (concedidos.isNotEmpty() && concedidos[0] == PackageManager.PERMISSION_GRANTED) {
            conceder?.invoke()
        } else {
            negar?.invoke()
        }
        return true
    }

    // -------------------------------------------------------------------------
    // Las emparejadas
    // -------------------------------------------------------------------------

    private fun adaptador(): BluetoothAdapter? =
        (actividad.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    @SuppressLint("MissingPermission")
    private fun emparejadas(): Map<String, Any> {
        val bt = adaptador() ?: return falla("sin_bluetooth", "este teléfono no tiene Bluetooth")
        if (!bt.isEnabled) return falla("bluetooth_apagado", "el Bluetooth está apagado")
        val lista = bt.bondedDevices.orEmpty().map { equipo ->
            mapOf(
                "nombre" to (equipo.name ?: equipo.address),
                "direccion" to equipo.address,
                // Las impresoras se anuncian como equipos de imagen; con eso la
                // app las pone primero, sin esconder las demás (más de una
                // térmica barata se anuncia como «sin clase»).
                "es_impresora" to
                    (equipo.bluetoothClass?.majorDeviceClass == BluetoothClass.Device.Major.IMAGING),
            )
        }
        return mapOf("codigo" to "ok", "impresoras" to lista)
    }

    // -------------------------------------------------------------------------
    // Imprimir
    // -------------------------------------------------------------------------

    @SuppressLint("MissingPermission")
    private fun imprimir(direccion: String, bytes: ByteArray, resultado: MethodChannel.Result) {
        val bt = adaptador()
        if (bt == null) {
            resultado.success(falla("sin_bluetooth", "este teléfono no tiene Bluetooth"))
            return
        }
        if (!bt.isEnabled) {
            resultado.success(falla("bluetooth_apagado", "el Bluetooth está apagado"))
            return
        }
        if (!BluetoothAdapter.checkBluetoothAddress(direccion)) {
            resultado.success(falla("datos", "la dirección de la impresora no es válida"))
            return
        }
        // Conectar tarda: fuera del hilo de la pantalla, y la respuesta de regreso
        // en él, que es donde Flutter la espera.
        hilo.execute {
            val respuesta = try {
                mandar(bt.getRemoteDevice(direccion), bytes)
            } catch (e: Exception) {
                falla("error", e.message ?: e.javaClass.simpleName)
            }
            principal.post { resultado.success(respuesta) }
        }
    }

    private fun mandar(impresora: BluetoothDevice, bytes: ByteArray): Map<String, Any> {
        val socket = conectar(impresora)
            ?: return falla("no_conecta", "la impresora no contestó")
        try {
            val salida = socket.outputStream
            var enviados = 0
            while (enviados < bytes.size) {
                val cuantos = minOf(TROZO, bytes.size - enviados)
                salida.write(bytes, enviados, cuantos)
                salida.flush()
                enviados += cuantos
                Thread.sleep(20)
            }
            // Cerrar en cuanto se escribe corta lo que la impresora todavía no
            // termina: se le da tiempo, más mientras más largo el ticket.
            Thread.sleep(minOf(3000L, 400L + bytes.size / 4L))
            return mapOf("codigo" to "ok", "bytes" to bytes.size)
        } catch (e: IOException) {
            return falla("error_escritura", e.message ?: "se cortó la conexión")
        } finally {
            try {
                socket.close()
            } catch (ignorada: IOException) {
                // Ya se mandó lo que se pudo.
            }
        }
    }

    /**
     * El socket, por el primer camino que funcione: el seguro, el inseguro, y el
     * canal 1 directo para las térmicas que no anuncian su servicio.
     */
    @SuppressLint("MissingPermission")
    private fun conectar(impresora: BluetoothDevice): BluetoothSocket? {
        val caminos = listOf<() -> BluetoothSocket>(
            { impresora.createRfcommSocketToServiceRecord(SPP) },
            { impresora.createInsecureRfcommSocketToServiceRecord(SPP) },
            {
                impresora.javaClass
                    .getMethod("createRfcommSocket", Int::class.javaPrimitiveType)
                    .invoke(impresora, 1) as BluetoothSocket
            },
        )
        for (crear in caminos) {
            var socket: BluetoothSocket? = null
            try {
                socket = crear()
                socket.connect()
                return socket
            } catch (e: Exception) {
                try {
                    socket?.close()
                } catch (ignorada: IOException) {
                    // Se intenta el siguiente camino.
                }
            }
        }
        return null
    }

    private fun falla(codigo: String, detalle: String): Map<String, Any> =
        mapOf("codigo" to codigo, "detalle" to detalle)
}
