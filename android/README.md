# GPS Libre — Android

Cambia la ubicación GPS de un celular Android, **sin PC**. Android trae de fábrica la «ubicación
simulada»: esta app la usa para poner el GPS donde tú quieras (teletransporte, rutas por calles y
joystick), todo dentro del teléfono.

Usa la **misma licencia que la app de iPhone**: las claves que genera `gps-simulador/empaquetar/generar_licencia.py`
sirven para iPhone y para Android (misma llave de firma).

## Cómo está hecha

- **WebView + mapa** (`app/src/main/assets/web/index.html`): la interfaz es el mismo mapa de siempre (Leaflet + OpenStreetMap), adaptado a celular.
- **`UbicacionService.kt`**: servicio en primer plano que fija la ubicación falsa y la mantiene aunque cambies a otra app (para abrir la app de citas, por ejemplo).
- **`Licencia.kt`**: 2 teletransportes gratis; rutas y joystick son de la versión completa; activación con clave firmada (Ed25519, misma llave pública que el iPhone).
- **`PuenteWeb.kt`**: conecta el mapa (JavaScript) con Android.

## Compilar el APK en la nube (sin instalar nada pesado)

Tu PC no necesita Android Studio. GitHub compila la app por ti y te da el APK:

1. Crea una cuenta gratis en **github.com**.
2. Crea un repositorio nuevo (privado está bien), por ejemplo `gps-libre-android`.
3. Sube esta carpeta al repositorio. Si no usas Git, en la web del repo: **Add file → Upload files**, arrastra **todo el contenido de esta carpeta** (incluida la carpeta oculta `.github`) y confirma.
4. Ve a la pestaña **Actions** del repositorio. Verás el flujo **«Construir APK»** ejecutándose (o pulsa **Run workflow**).
5. Cuando termine (unos minutos), entra a esa ejecución y abajo, en **Artifacts**, descarga **`GPSLibre-APK`**. Dentro está `app-debug.apk`.

> La primera vez es normal que falle por algún detalle de versiones. Copia el error del log de Actions y se corrige.

## Instalar y usar en el celular Android

1. Pasa el `app-debug.apk` al celular y ábrelo. Android pedirá permitir **«instalar apps de orígenes desconocidos»** (es normal en apps fuera de Play Store): acéptalo.
2. Abre GPS Libre. Sigue los 3 pasos que muestra:
   - **Opciones de desarrollador**: Ajustes → Acerca del teléfono → toca 7 veces «Número de compilación».
   - **App de ubicación simulada**: Ajustes → Opciones de desarrollador → «Seleccionar app de ubicación simulada» → **GPS Libre**.
   - Toca el mapa → **Mover aquí**.
3. Abre tu app de mapas (o la que quieras) y verás la ubicación falsa.

Es un **APK de depuración sin firmar**: sirve perfecto para probar y repartir a mano. Para subirlo a
Google Play hay que firmarlo (se hace después, cuando decidas publicar).

## Pendiente / notas

- **Sin probar todavía**: se escribió sin un Android a mano, así que la primera compilación puede necesitar 1-2 correcciones.
- **Suscripciones**: igual que en iPhone, el cobro va por Lemon Squeezy (renovación automática y cancelación fácil las trae la pasarela). Cuando esté la cuenta, se conecta la validación en línea y el enlace de compra.
- **Play Store**: cuenta de desarrollador de Google (pago único ~25 USD) + firma del APK. Opcional; se puede repartir el APK directo mientras tanto.
