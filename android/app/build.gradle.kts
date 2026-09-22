plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.gpslibre.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.gpslibre.app"
        minSdk = 26          // Android 8.0: cubre casi todos los teléfonos en uso
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false   // sin ofuscar: la app es pequeña y así el APK sin firmar arranca seguro
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    // Verificación de la clave de licencia (Ed25519), fiable en cualquier versión de Android.
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
}
