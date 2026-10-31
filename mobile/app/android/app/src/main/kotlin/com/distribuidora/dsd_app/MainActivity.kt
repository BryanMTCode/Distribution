package com.distribuidora.dsd_app

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    // La impresora térmica por Bluetooth (ADR 0002 §139).
    private var impresora: ImpresoraBluetooth? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        impresora = ImpresoraBluetooth(this, flutterEngine.dartExecutor.binaryMessenger)
        // La ubicación de un cliente, a la app de mapas del teléfono (ADR 0002
        // §129). Un Intent `geo:` y nada más: sin SDK de mapas dentro del APK.
        // Android ofrece las que tenga (Google Maps, Waze, Organic Maps) y
        // recuerda la que se elija.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "dsd/mapas")
            .setMethodCallHandler { llamada, resultado ->
                if (llamada.method != "abrir") {
                    resultado.notImplemented()
                    return@setMethodCallHandler
                }
                val lat = llamada.argument<String>("lat")
                val lng = llamada.argument<String>("lng")
                val nombre = llamada.argument<String>("nombre") ?: ""
                if (lat == null || lng == null) {
                    resultado.success(false)
                    return@setMethodCallHandler
                }
                // `geo:0,0?q=lat,lng(nombre)`: el punto con su nombre, que es lo
                // que entienden todas; desde ahí cada app ofrece «Cómo llegar».
                val etiqueta = Uri.encode(nombre.replace("(", " ").replace(")", " "))
                val intento = Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse("geo:0,0?q=$lat,$lng($etiqueta)")
                )
                try {
                    startActivity(intento)
                    resultado.success(true)
                } catch (e: ActivityNotFoundException) {
                    resultado.success(false)
                }
            }
        // Una dirección web, al navegador del teléfono (ADR 0002 §194): el Excel
        // del dashboard, que el navegador sabe bajar y abrir. Solo http(s).
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "dsd/navegador")
            .setMethodCallHandler { llamada, resultado ->
                val url = llamada.argument<String>("url")
                if (llamada.method != "abrir" || url == null) {
                    resultado.notImplemented()
                    return@setMethodCallHandler
                }
                val direccion = Uri.parse(url)
                if (direccion.scheme != "https" && direccion.scheme != "http") {
                    resultado.success(false)
                    return@setMethodCallHandler
                }
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, direccion))
                    resultado.success(true)
                } catch (e: ActivityNotFoundException) {
                    resultado.success(false)
                }
            }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray,
    ) {
        // El del Bluetooth lo atiende la impresora; los demás (la ubicación), los
        // plugins, por el camino de siempre.
        if (impresora?.alContestarPermiso(requestCode, grantResults) == true) return
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    }
}
