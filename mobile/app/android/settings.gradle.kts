pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

// Las tres versiones de abajo, más la de Gradle en
// `gradle/wrapper/gradle-wrapper.properties`, son EL ESTÁNDAR de este proyecto y
// se subieron juntas porque Flutter no compilaba el APK de release con las
// anteriores (Gradle 8.12 / AGP 8.9.1 / Kotlin 2.1.0). Van versionadas aquí a
// propósito: la primera vez este ajuste vivió solo en una máquina y un
// `git reset` se lo llevó.
//
// Si alguna se baja, el build vuelve a fallar con errores de compatibilidad que
// no mencionan la versión. Súbanse las cuatro juntas, nunca una sola.
plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.11.1" apply false
    id("org.jetbrains.kotlin.android") version "2.2.20" apply false
}

include(":app")
