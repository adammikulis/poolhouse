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

    // The screens are tested on the JVM (src/test) with the app's own resources and manifest.
    testOptions { unitTests { isIncludeAndroidResources = true } }
}

tasks.withType<Test>().configureEach {
    // Robolectric fetches the Android it runs on into the build's own folder, not the account's Maven folder.
    systemProperty("maven.repo.local", gradle.gradleUserHomeDir.resolve("robolectric-m2").path)
    testLogging { events("failed") }
}

dependencies {
    implementation(project(":core"))
    implementation(project(":theme"))
    // The model on the phone: found at run time by name (core/device/PhonePlugin.kt), so no screen of the app depends on it.
    runtimeOnly(project(":llm"))

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

    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    debugImplementation(libs.compose.ui.test.manifest)
}
