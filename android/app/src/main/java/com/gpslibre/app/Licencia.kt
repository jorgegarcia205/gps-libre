package com.gpslibre.app

import android.content.Context
import android.util.Base64
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.json.JSONArray
import org.json.JSONObject

/**
 * Prueba gratis (2 teletransportes) y luego activación con clave firmada.
 *
 * Es la MISMA licencia que la app de iPhone: la misma llave pública y el mismo formato de clave, así
 * que las claves que genera `empaquetar/generar_licencia.py` sirven para iPhone y para Android.
 */
object Licencia {
    // Llave pública para verificar las claves (la privada solo la tiene Jorge, en generar_licencia.py).
    private const val CLAVE_PUBLICA = "NClfWrRdw5YFZ5AvyEi9Vd4znf-B3-ojU3rRWoK-lMM"
    const val LIMITE_GRATIS = 2
    private const val DESCUENTO = "20% de descuento en tu primera compra"
    private val PRECIOS = listOf(
        Triple("Mensual", "$9,99", false),
        Triple("Trimestral", "$19,99", false),
        Triple("Anual", "$29,99", true),
    )

    private fun prefs(c: Context) = c.getSharedPreferences("licencia", Context.MODE_PRIVATE)

    private fun b64d(s: String): ByteArray {
        val pad = (4 - s.length % 4) % 4
        return Base64.decode(s + "=".repeat(pad), Base64.URL_SAFE or Base64.NO_WRAP)
    }

    /** Devuelve la carga {p, exp?, id} si la clave está bien firmada y no ha caducado; si no, null. */
    private fun validar(clave: String?): JSONObject? {
        if (clave.isNullOrBlank()) return null
        return try {
            var cuerpo = clave.trim()
            if (cuerpo.startsWith("GPSL-")) cuerpo = cuerpo.substring(5)
            val punto = cuerpo.indexOf('.')
            if (punto < 0) return null
            val carga = b64d(cuerpo.substring(0, punto))
            val firma = b64d(cuerpo.substring(punto + 1))
            val pub = Ed25519PublicKeyParameters(b64d(CLAVE_PUBLICA), 0)
            val verificador = Ed25519Signer()
            verificador.init(false, pub)
            verificador.update(carga, 0, carga.size)
            if (!verificador.verifySignature(firma)) return null
            val obj = JSONObject(String(carga, Charsets.UTF_8))
            if (obj.has("exp") && System.currentTimeMillis() / 1000 > obj.getLong("exp")) return null
            obj
        } catch (e: Exception) {
            null
        }
    }

    fun licenciaActiva(c: Context): JSONObject? = validar(prefs(c).getString("clave", null))

    fun activar(c: Context, clave: String): Boolean {
        if (validar(clave) == null) return false
        prefs(c).edit().putString("clave", clave.trim()).apply()
        return true
    }

    /** Cobra un movimiento. Solo los teletransportes son gratis; ruta y joystick son de la versión completa.
     *  Devuelve null si se permite, o un mensaje para el muro de compra si hay que activar. */
    fun cobrar(c: Context, tipo: String): String? {
        if (licenciaActiva(c) != null) return null
        if (tipo != "teletransporte") {
            return if (tipo == "ruta") "Las rutas son parte de GPS Libre completo. Actívalo para recorrerlas."
            else "El joystick es parte de GPS Libre completo. Actívalo para usarlo."
        }
        val usados = prefs(c).getInt("movimientos", 0)
        if (usados >= LIMITE_GRATIS) {
            return "Usaste tus $LIMITE_GRATIS teletransportes gratis. Activa GPS Libre para seguir."
        }
        prefs(c).edit().putInt("movimientos", usados + 1).apply()
        return null
    }

    fun estadoJson(c: Context): String {
        val activa = licenciaActiva(c)
        val precios = JSONArray()
        for ((nombre, precio, destacado) in PRECIOS) {
            precios.put(JSONObject().put("nombre", nombre).put("precio", precio).put("destacado", destacado))
        }
        val usados = prefs(c).getInt("movimientos", 0)
        return JSONObject()
            .put("licenciado", activa != null)
            .put("plan", activa?.optString("p"))
            .put("movimientos_usados", usados)
            .put("limite_gratis", LIMITE_GRATIS)
            .put("restantes", if (activa != null) JSONObject.NULL else maxOf(0, LIMITE_GRATIS - usados))
            .put("precios", precios)
            .put("descuento", DESCUENTO)
            .toString()
    }
}
