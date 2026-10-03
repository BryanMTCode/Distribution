import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// ---------------------------------------------------------------------------
// La firma del APK de producción
// ---------------------------------------------------------------------------
// Antes, aquí decía esto —tal cual lo deja la plantilla de Flutter—:
//
//     release {
//         // TODO: Add your own signing config for the release build.
//         signingConfig = signingConfigs.getByName("debug")
//     }
//
// Y eso NO falla: produce un APK de release instalable, firmado con la llave de
// depuración que Flutter genera sola en `~/.android/debug.keystore`. El daño
// aparece meses después, y por eso es la clase de defecto que más caro sale:
//
//   · Esa llave es distinta en cada máquina y se regenera sin avisar. El día que
//     se compile desde otra PC —o se borre esa carpeta— el APK nuevo ya no podrá
//     actualizar el que está instalado.
//   · Android solo acepta la actualización si la firma COINCIDE. Si no,
//     «App not installed», y el único camino es desinstalar.
//   · Desinstalar BORRA la base local del vendedor. Con ella se van las ventas,
//     los cobros y las mermas que todavía no hubieran subido: dinero que ocurrió
//     y que ya no está en ninguna cifra.
//
// Así que ahora la firma de release sale de `android/key.properties`, que no se
// versiona, y si no está EL BUILD DE RELEASE SE DETIENE. Un APK de producción
// mal firmado no debe poder existir por omisión.
val clavesDeFirma = Properties().apply {
    val archivo = rootProject.file("key.properties")
    if (archivo.exists()) archivo.inputStream().use { load(it) }
}

// Un valor vacío cuenta como ausente: `key.properties.example` trae las dos
// contraseñas en blanco, y una copia sin rellenar debe fallar aquí —con el
// mensaje de abajo— y no doscientas líneas después con un error de keystore.
fun clave(nombre: String): String? =
    clavesDeFirma.getProperty(nombre)?.takeIf { it.isNotBlank() }

// `rootProject.file` y no `file`: el segundo resuelve lo relativo contra
// `android/app/`, que no es donde nadie esperaría apuntar. Una ruta absoluta
// —lo normal para un keystore que vive fuera del repositorio— pasa igual por
// los dos.
val rutaDelAlmacen = clave("storeFile")
val almacen = rutaDelAlmacen?.let { rootProject.file(it) }
val faltanDatos = listOf("storeFile", "storePassword", "keyAlias", "keyPassword")
    .filter { clave(it) == null }
val hayFirmaDeProduccion = faltanDatos.isEmpty() && almacen!!.exists()

// Se comprueba cuando ya se sabe QUÉ se va a construir, no al configurar: si el
// error saltara al configurar, `flutter run` y las pruebas dejarían de funcionar
// en cualquier máquina sin keystore, que es la mayoría y está bien que lo sea.
gradle.taskGraph.whenReady {
    val construyeRelease = allTasks.any { it.name.contains("Release") }
    if (construyeRelease && !hayFirmaDeProduccion) {
        val porQue = when {
            !rootProject.file("key.properties").exists() ->
                "falta android/key.properties (copia key.properties.example)"
            faltanDatos.isNotEmpty() ->
                "key.properties está sin rellenar: ${faltanDatos.joinToString(", ")}"
            else ->
                "key.properties apunta a $rutaDelAlmacen y ese archivo no existe"
        }
        throw GradleException(
            """

            No hay con qué firmar este APK de producción: $porQue.

            Firmar un release con la llave de depuración SÍ funciona hoy y rompe
            la actualización mañana: esa llave es distinta en cada máquina, y
            cuando no coincida habrá que desinstalar la app — lo que borra la
            base local del vendedor y con ella las ventas que no hubiera subido.

            Crea el keystore y el key.properties: docs/ENTORNO-WINDOWS.md §4.2
            Para medir rendimiento sin keystore:  flutter run --profile
            """.trimIndent()
        )
    }
}

android {
    namespace = "com.distribuidora.dsd_app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_11.toString()
    }

    defaultConfig {
        applicationId = "com.distribuidora.dsd_app"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion

        // Vienen de `version:` en pubspec.yaml. El número después del `+` es el
        // versionCode, y Android RECHAZA instalar encima un APK cuyo
        // versionCode sea MENOR que el instalado. Con el mismo número sí
        // reinstala, y ahí está el otro problema: dos APK distintos con el
        // mismo versionCode son indistinguibles con el teléfono en la mano, y
        // «¿qué versión trae este equipo?» deja de tener respuesta. Súbelo en
        // pubspec.yaml antes de repartir un APK nuevo.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (hayFirmaDeProduccion) {
            create("release") {
                storeFile = almacen
                storePassword = clave("storePassword")
                keyAlias = clave("keyAlias")
                keyPassword = clave("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // Si no hay firma de produccion, el `whenReady` de arriba ya detuvo
            // el build. Esta rama no deja un APK a medias: no llega a correr.
            if (hayFirmaDeProduccion) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }
}

flutter {
    source = "../.."
}
