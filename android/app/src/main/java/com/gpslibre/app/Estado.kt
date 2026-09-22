package com.gpslibre.app

/** Estado compartido entre la interfaz (WebView) y el servicio que empuja la ubicación falsa. */
object Estado {
    @Volatile var activo = false
    @Volatile var lat = 0.0
    @Volatile var lon = 0.0
    @Volatile var error: String? = null
}
