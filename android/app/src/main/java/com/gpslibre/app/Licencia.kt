package com.gpslibre.app

import android.content.Context
import android.util.Base64
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Prueba gratis (2 teletransportes) y luego activación con clave.
 *
 * Acepta DOS tipos de clave, igual que la app de iPhone:
 *   1) Clave firmada nuestra (GPSL-…): dueño y accesos manuales. Se verifica sin internet con la
 *      llave pública Ed25519 (la privada solo la tiene Jorge, en generar_licencia.py).
 *   2) Clave de Lemon Squeezy (UUID): la que compra un cliente. Se activa y revalida en línea contra
 *      api.lemonsqueezy.com/v1/licenses (no requiere token, solo la clave). El estado se guarda en
 *      caché y hay gracia offline: si no hay internet, se conserva el último estado conocido.
 */
object Licencia {
    // Llave pública para verificar las claves GPSL- (la privada solo la tiene Jorge).
    private const val CLAVE_PUBLICA = "NClfWrRdw5YFZ5AvyEi9Vd4znf-B3-ojU3rRWoK-lMM"
    const val LIMITE_GRATIS = 2
    private const val DESCUENTO = "20% de descuento en tu primera compra"
    private const val LS_API = "https://api.lemonsqueezy.com/v1/licenses"
    private const val REVALIDAR_MS = 6L * 3600L * 1000L  // revalida las claves LS cada 6 h
    private val PRECIOS = listOf(
        Triple("Mensual", "$9,99", false),
        Triple("Trimestral", "$19,99", false),
        Triple("Anual", "$29,99", true),
    )

    @Volatile private var revalidando = false

    private fun prefs(c: Context) = c.getSharedPreferences("licencia", Context.MODE_PRIVATE)

    private fun b64d(s: String): ByteArray {
        val pad = (4 - s.length % 4) % 4
        return Base64.decode(s + "=".repeat(pad), Base64.URL_SAFE or Base64.NO_WRAP)
    }

    // ----------------------------------------------------------------------------------
    // 1) Clave firmada nuestra (GPSL-)
    // ----------------------------------------------------------------------------------

    /** Devuelve la carga {p, exp?, id} si la clave GPSL- está bien firmada y no ha caducado; si no, null. */
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

    // ----------------------------------------------------------------------------------
    // 2) Clave de Lemon Squeezy (UUID) — activación y revalidación en línea
    // ----------------------------------------------------------------------------------

    /** Llama a api.lemonsqueezy.com/v1/licenses/<endpoint> con parámetros de formulario. */
    private fun lsPeticion(endpoint: String, params: Map<String, String>): JSONObject? {
        var con: HttpURLConnection? = null
        return try {
            val cuerpo = params.entries.joinToString("&") {
                "${URLEncoder.encode(it.key, "UTF-8")}=${URLEncoder.encode(it.value, "UTF-8")}"
            }
            con = (URL("$LS_API/$endpoint").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = 10000
                readTimeout = 10000
                setRequestProperty("Accept", "application/json")
                setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
            }
            con.outputStream.use { it.write(cuerpo.toByteArray(Charsets.UTF_8)) }
            // LS responde JSON incluso en 400 (clave inválida): leemos del stream que exista.
            val stream = try { con.inputStream } catch (e: Exception) { con.errorStream }
            val texto = stream?.bufferedReader()?.use(BufferedReader::readText) ?: return null
            JSONObject(texto)
        } catch (e: Exception) {
            null
        } finally {
            con?.disconnect()
        }
    }

    /** Interpreta la respuesta de LS. `expira` en milisegundos (0 = sin caducidad). */
    private fun interpretarLs(r: JSONObject?, instanceId: String?): Map<String, Any?> {
        val lk = r?.optJSONObject("license_key")
        val meta = r?.optJSONObject("meta")
        val estado = lk?.optString("status")
        val activo = (r?.optBoolean("valid", false) == true || r?.optBoolean("activated", false) == true) &&
            estado == "active"
        var expiraMs = 0L
        val expira = lk?.optString("expires_at")
        if (!expira.isNullOrBlank() && expira != "null") {
            expiraMs = try {
                // ISO-8601, p. ej. "2026-12-31T23:59:59.000000Z"
                val limpio = expira.replace("Z", "+00:00")
                java.time.OffsetDateTime.parse(limpio).toInstant().toEpochMilli()
            } catch (e: Exception) {
                0L
            }
        }
        val inst = r?.optJSONObject("instance")?.optString("id")?.takeIf { it.isNotBlank() } ?: instanceId
        return mapOf(
            "activo" to activo,
            "expira" to expiraMs,
            "plan" to (meta?.optString("product_name")?.takeIf { it.isNotBlank() } ?: "premium"),
            "instance_id" to inst,
            "status" to (estado ?: "inactive"),
        )
    }

    /** Revalida en segundo plano una clave LS ya guardada (gracia offline: si no hay red, no toca nada). */
    private fun revalidarLs(c: Context) {
        val p = prefs(c)
        val clave = p.getString("clave", null)
        if (p.getString("tipo", null) != "ls" || clave.isNullOrBlank()) return
        val r = lsPeticion("validate", mapOf(
            "license_key" to clave,
            "instance_id" to (p.getString("instance_id", "") ?: ""),
        )) ?: return  // sin internet: conserva el último estado conocido
        val info = interpretarLs(r, p.getString("instance_id", null))
        p.edit()
            .putString("estado_ls", if (info["activo"] == true) "active" else (info["status"] as? String ?: "inactive"))
            .putLong("expira_ls", info["expira"] as? Long ?: 0L)
            .putLong("validado_ls", System.currentTimeMillis())
            .apply()
    }

    /** Lanza una revalidación LS en un hilo aparte si toca (>6 h desde la última). No bloquea. */
    private fun revalidarLsSiToca(c: Context) {
        val p = prefs(c)
        if (p.getString("tipo", null) != "ls") return
        val ultima = p.getLong("validado_ls", 0L)
        if (System.currentTimeMillis() - ultima < REVALIDAR_MS) return
        if (revalidando) return
        revalidando = true
        Thread {
            try { revalidarLs(c) } catch (e: Exception) { /* se reintenta la próxima vez */ }
            finally { revalidando = false }
        }.apply { isDaemon = true }.start()
    }

    // ----------------------------------------------------------------------------------
    // API pública
    // ----------------------------------------------------------------------------------

    /** Licencia vigente (o null). Para claves LS refresca el estado en segundo plano sin bloquear. */
    fun licenciaActiva(c: Context): JSONObject? {
        // 1) Nuestra clave firmada (GPSL-)
        val gpsl = validar(prefs(c).getString("clave", null))
        if (gpsl != null) return gpsl
        // 2) Clave de Lemon Squeezy (estado en caché; se refresca solo)
        val p = prefs(c)
        if (p.getString("tipo", null) == "ls" && p.getString("estado_ls", null) == "active") {
            val exp = p.getLong("expira_ls", 0L)
            if (exp > 0L && System.currentTimeMillis() > exp) return null
            revalidarLsSiToca(c)
            return JSONObject()
                .put("p", p.getString("plan_ls", "premium"))
                .put("exp", if (exp > 0L) exp / 1000 else JSONObject.NULL)
                .put("ls", true)
        }
        return null
    }

    /**
     * Activa una clave. Acepta GPSL- (offline) o de Lemon Squeezy (en línea).
     * OJO: hace red, así que debe llamarse fuera del hilo principal (el puente WebView ya corre
     * en un hilo aparte, así que desde `activarLicencia` es seguro).
     */
    fun activar(c: Context, clave: String): Boolean {
        val limpia = clave.trim()
        // 1) Clave firmada nuestra (dueño / accesos manuales)
        if (validar(limpia) != null) {
            prefs(c).edit()
                .putString("clave", limpia)
                .putString("tipo", "gpsl")
                .remove("instance_id").remove("estado_ls").remove("expira_ls")
                .remove("plan_ls").remove("validado_ls")
                .apply()
            return true
        }
        // 2) Clave de Lemon Squeezy: activa/valida en línea
        val r = lsPeticion("activate", mapOf(
            "license_key" to limpia,
            "instance_name" to "GPS Libre Android",
        )) ?: return false  // sin conexión
        var info = interpretarLs(r, null)
        if (info["activo"] != true) {
            // p. ej. ya alcanzó el límite de activaciones: intenta solo validar
            val r2 = lsPeticion("validate", mapOf("license_key" to limpia))
            info = interpretarLs(r2, null)
        }
        if (info["activo"] != true) return false
        prefs(c).edit()
            .putString("clave", limpia)
            .putString("tipo", "ls")
            .putString("instance_id", info["instance_id"] as? String)
            .putString("estado_ls", "active")
            .putLong("expira_ls", info["expira"] as? Long ?: 0L)
            .putString("plan_ls", info["plan"] as? String ?: "premium")
            .putLong("validado_ls", System.currentTimeMillis())
            .apply()
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
