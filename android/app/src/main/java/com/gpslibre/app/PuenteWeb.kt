package com.gpslibre.app

import android.app.Activity
import android.content.Intent
import android.provider.Settings
import android.webkit.JavascriptInterface

/**
 * Puente entre el mapa (WebView, en JavaScript) y Android. El mapa llama a `Android.poner(...)`,
 * `Android.cobrar(...)`, etc. Toda la lógica de mover el GPS y la licencia vive del lado nativo.
 */
class PuenteWeb(private val actividad: Activity) {

    @JavascriptInterface
    fun estadoLicencia(): String = Licencia.estadoJson(actividad)

    @JavascriptInterface
    fun activarLicencia(clave: String): Boolean = Licencia.activar(actividad, clave)

    /** Cobra un movimiento. Devuelve "" si se permite, o el texto del muro de compra si hay que activar. */
    @JavascriptInterface
    fun cobrar(tipo: String): String = Licencia.cobrar(actividad, tipo) ?: ""

    /** Fija la ubicación simulada y arranca el servicio que la mantiene. */
    @JavascriptInterface
    fun poner(lat: Double, lon: Double) {
        Estado.lat = lat
        Estado.lon = lon
        Estado.activo = true
        UbicacionService.iniciar(actividad)
    }

    /** Vuelve al GPS real. */
    @JavascriptInterface
    fun detener() {
        Estado.activo = false
        UbicacionService.detener(actividad)
    }

    /** "" si todo bien; "mock" si el usuario no eligió esta app como app de ubicación simulada; o un mensaje. */
    @JavascriptInterface
    fun error(): String = Estado.error ?: ""

    /** Abre Opciones de desarrollador para elegir GPS Libre como app de ubicación simulada. */
    @JavascriptInterface
    fun abrirAjustesDesarrollador() {
        try {
            actividad.startActivity(
                Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            actividad.startActivity(
                Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }
}
