pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "poolhouse-android"

// :core is the protocol and the app's state in plain Kotlin (no Android class); :theme is the look every screen shares; :app is the
// screens; :llm is the model on the phone, which :app finds at run time (core/device/PhonePlugin.kt).
include(":core", ":theme", ":llm", ":app")
