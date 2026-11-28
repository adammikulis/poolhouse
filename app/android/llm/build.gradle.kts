import java.security.MessageDigest

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
}

// The on-device runtimes, pinned by version and by the bytes of their archives.
val litertLm = "0.20.0"
val pinnedArchives = mapOf(
    "litertlm-android-0.20.0.aar" to "9cd2699d796a7120ff567316de85e797250cc10b2b67cb616c48567b719e7ed8",
    "litert-2.4.0.aar" to "13926c98f35417b6fe4f104f780ab869747b4a69f08a91ccd224013c6bf71615",
    "litert-gpu-2.4.0.aar" to "9bf935660d3ba33c45e43345fe77b7f4cc0d8f63ec8e007e45994863254385b2",
)

android {
    namespace = "app.poolhouse.llm"
    compileSdk = providers.gradleProperty("androidApi").map { it.toInt() }.getOrElse(36)
    defaultConfig {
        minSdk = 31
        ndk { abiFilters += "arm64-v8a" }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
    // The llama.cpp libraries `native/build.py` makes; a build without them offers no GGUF model.
    sourceSets.getByName("main").jniLibs.directories.add("build/llama-jni")
}

val checkPinnedArchives = tasks.register("checkPinnedArchives") {
    val files = configurations.named("debugRuntimeClasspath").map { classpath ->
        classpath.incoming.artifactView {
            attributes.attribute(Attribute.of("artifactType", String::class.java), "aar")
        }.files
    }
    inputs.files(files)
    doLast {
        val found = files.get().filter { it.name in pinnedArchives }.associateBy { it.name }
        pinnedArchives.forEach { (name, expected) ->
            val file = found[name] ?: throw GradleException("The pinned archive $name is not in this build.")
            val actual = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
            if (actual != expected) throw GradleException("$name has checksum $actual, not the pinned $expected.")
        }
    }
}

tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(checkPinnedArchives) }

dependencies {
    implementation(project(":core"))
    implementation("com.google.ai.edge.litertlm:litertlm-android:$litertLm") { version { strictly(litertLm) } }
    implementation(libs.coroutines.android)
    implementation(libs.serialization.json)
    implementation(libs.activity.compose)
    implementation(libs.lifecycle.runtime.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
}
