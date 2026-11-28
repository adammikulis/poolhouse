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

// :core is the protocol and the app's state in plain Kotlin (no Android class); :app is the screens.
include(":core", ":app")
// The on-device model lives in its own module, which the app finds at run time (core/device/PhonePlugin.kt).
if (file("llm/build.gradle.kts").isFile) include(":llm")
