plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// The app's version is the product's, read from pyproject.toml.
val productVersion: String = Regex("(?m)^version = \"([^\"]+)\"")
    .find(rootProject.projectDir.resolve("../../pyproject.toml").readText())!!.groupValues[1]
val versionNumbers: List<Int> = productVersion.split('.', '-').take(3).map { part -> part.takeWhile { it.isDigit() }.toIntOrNull() ?: 0 }

android {
    namespace = "app.poolhouse.phone"
    compileSdk { version = release(37) { minorApiLevel = 0 } }
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "app.poolhouse.phone"
        // Android 12: the least the on-device model's runtime (:llm) runs on.
        minSdk = 31
        targetSdk = 37
        versionName = productVersion
        versionCode = versionNumbers[0] * 1_000_000 + versionNumbers[1] * 1_000 + versionNumbers[2]
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    val keystore = System.getenv("ANDROID_SIGNING_STORE")
    signingConfigs {
        if (keystore != null) {
            create("preview") {
                storeFile = file(keystore)
                storePassword = System.getenv("ANDROID_SIGNING_PASSWORD")
                keyAlias = "poolhouse-preview"
                keyPassword = System.getenv("ANDROID_SIGNING_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("preview")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures { compose = true }
}

dependencies {
    implementation(project(":core"))
    // The on-device model, when this build includes it: found at run time by name (core/device/PhonePlugin.kt).
    if (rootProject.findProject(":llm") != null) runtimeOnly(project(":llm"))

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.coroutines.android)
    implementation(libs.camera.camera2)
    implementation(libs.camera.lifecycle)
    implementation(libs.camera.view)
    implementation(libs.zxing.core)

    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.ext.junit)
    debugImplementation(libs.compose.ui.test.manifest)
}
