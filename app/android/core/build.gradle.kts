import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    api(libs.coroutines.core)
    api(libs.serialization.json)
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
}

/** Writes the classpath of the sample computer (src/test/kotlin/.../SampleDay.kt), to run it with `java` outside a build. */
tasks.register("sampleClasspath") {
    dependsOn(tasks.named("testClasses"))
    val classpath = sourceSets.test.get().runtimeClasspath
    val out = layout.buildDirectory.file("sample-classpath.txt")
    inputs.files(classpath)
    outputs.file(out)
    doLast { out.get().asFile.writeText(classpath.asPath) }
}

tasks.test {
    // The contract's worked examples are read from the repository, so the app and the computer are held to one document.
    systemProperty("poolhouse.root", rootProject.projectDir.resolve("../..").canonicalPath)
    testLogging { events("failed") }
}
