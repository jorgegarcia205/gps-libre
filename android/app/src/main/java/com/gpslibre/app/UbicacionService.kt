package com.gpslibre.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import androidx.core.app.NotificationCompat

/**
 * Servicio en primer plano que mantiene la ubicación simulada aunque el usuario cambie de app
 * (por ejemplo, para abrir la app de citas). Empuja la ubicación cada segundo al proveedor GPS
 * de prueba de Android; para que funcione, el usuario debe elegir GPS Libre como «app de ubicación
 * simulada» en Opciones de desarrollador.
 */
class UbicacionService : Service() {

    private lateinit var lm: LocationManager
    private val handler = Handler(Looper.getMainLooper())
    private var proveedorListo = false
    private val proveedor = LocationManager.GPS_PROVIDER

    private val bucle = object : Runnable {
        override fun run() {
            empujar()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        lm = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        crearCanal()
        startForeground(1, notificacion())
        prepararProveedor()
        handler.post(bucle)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    private fun prepararProveedor() {
        proveedorListo = try {
            try {
                lm.addTestProvider(
                    proveedor, false, false, false, false, true, true, true, 1, 2
                )
            } catch (_: Exception) {
                // Puede fallar si ya existe; no es problema.
            }
            lm.setTestProviderEnabled(proveedor, true)
            Estado.error = null
            true
        } catch (e: SecurityException) {
            Estado.error = "mock"   // la interfaz lo interpreta y guía al usuario a Opciones de desarrollador
            false
        } catch (e: Exception) {
            Estado.error = "No se pudo activar la ubicación simulada: ${e.message}"
            false
        }
    }

    private fun empujar() {
        if (!proveedorListo || !Estado.activo) return
        val loc = Location(proveedor).apply {
            latitude = Estado.lat
            longitude = Estado.lon
            accuracy = 4f
            time = System.currentTimeMillis()
            elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
            altitude = 0.0
            bearing = 0f
            speed = 0f
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                bearingAccuracyDegrees = 0.1f
                verticalAccuracyMeters = 0.1f
                speedAccuracyMetersPerSecond = 0.01f
            }
        }
        try {
            lm.setTestProviderLocation(proveedor, loc)
        } catch (_: Exception) {
        }
    }

    override fun onDestroy() {
        handler.removeCallbacks(bucle)
        try {
            lm.setTestProviderEnabled(proveedor, false)
        } catch (_: Exception) {
        }
        try {
            lm.removeTestProvider(proveedor)
        } catch (_: Exception) {
        }
        Estado.activo = false
        super.onDestroy()
    }

    private fun crearCanal() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val canal = NotificationChannel(
                CANAL, getString(R.string.canal_nombre), NotificationManager.IMPORTANCE_LOW
            )
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(canal)
        }
    }

    private fun notificacion(): Notification {
        val abrir = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CANAL)
            .setContentTitle(getString(R.string.noti_titulo))
            .setContentText(getString(R.string.noti_texto))
            .setSmallIcon(R.drawable.ic_noti)
            .setContentIntent(abrir)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CANAL = "gpslibre"

        fun iniciar(c: Context) {
            val i = Intent(c, UbicacionService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) c.startForegroundService(i) else c.startService(i)
        }

        fun detener(c: Context) {
            c.stopService(Intent(c, UbicacionService::class.java))
        }
    }
}
