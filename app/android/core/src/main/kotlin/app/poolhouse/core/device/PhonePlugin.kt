package app.poolhouse.core.device

import kotlinx.coroutines.flow.StateFlow

/*
 * What the app asks of the module that runs a model on the phone. The app has no dependency on that module's classes: when the build
 * includes it, the app creates [PLUGIN_CLASS] by name (a public class with no arguments) and shows what it offers; when it does not,
 * the phone only directs the computer and nothing about running a model is shown.
 *
 * `context` is the Android application context, typed `Any` because this module has no Android class.
 */

const val PLUGIN_CLASS = "app.poolhouse.llm.LlmPlugin"

interface PhonePlugin {
    /** The conversations and screens the module offers, listed with every other session. */
    fun entries(context: Any): List<PluginEntry>

    /** The one switch: whether the pool may use this phone, or it may act as a coding agent. Off until the person turns it on. */
    fun worker(context: Any, link: ComputerLink): PhoneWorker
}

/** A screen of the module: a row of the session list that opens `activity`, an Android activity class name, in this app. */
data class PluginEntry(val title: String, val subtitle: String, val activity: String)

interface PhoneWorker {
    val state: StateFlow<WorkerState>

    /** Turns the phone's part on or off. The module keeps the choice and reports what it is doing through [state]. */
    fun setEnabled(on: Boolean)

    /** Reads the switch from the computer again: the app calls this when it starts and when the computer says something changed. */
    fun refresh()
}

enum class WorkerPhase { OFF, STARTING, READY, BUSY, FAILED }

data class WorkerState(
    val phase: WorkerPhase,
    /** One sentence for the person: what the phone is doing now. */
    val detail: String,
    val model: String? = null,
    /** Why the phone is on and still takes no work (heat, battery, no model), or null. */
    val blocked: String? = null,
    /** What taking part costs the phone, in the module's words, or null. */
    val cost: String? = null,
) {
    val enabled: Boolean get() = phase != WorkerPhase.OFF
}

/** The module's way to the linked computer: the phone API (docs/phone-api.md) over the app's pinned, signed connection. */
fun interface ComputerLink {
    /**
     * Calls `/phone/v1/<op>` (for example `model/next` or `pool-use`) and returns the JSON of the result: a POST of the JSON `body`,
     * or a GET when `body` is null. `readTimeoutMs` must exceed the longest wait the call asks of the computer. Throws when the
     * computer refuses or cannot be reached.
     */
    suspend fun call(op: String, body: String?, readTimeoutMs: Long): String
}
