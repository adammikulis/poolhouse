package app.poolhouse.core.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/*
 * The shapes of the phone API, docs/phone-api.md. A field the page does not name is ignored, and every field that a computer may
 * leave out has a default, so a computer that says less is shown saying less and never as something invented.
 */

val PhoneJson = Json {
    ignoreUnknownKeys = true
    isLenient = false
    explicitNulls = false
    coerceInputValues = true
}

@Serializable
data class Hello(
    val service: String = "",
    val api: Int = 0,
    val computer: String = "",
    @SerialName("now_ms") val nowMs: Long = 0,
    val linking: Boolean = false,
    /** Set only by the sample server the app is checked against (tests/phone_fixture.py): everything it answers is canned. */
    val canned: Boolean = false,
)

@Serializable
data class LinkRequest(val name: String, val key: String, val proof: String, val signature: String)

@Serializable
data class LinkResult(val phone: String, val computer: String = "", val words: String = "", val proof: String)

@Serializable
data class Me(
    val phone: String = "",
    val name: String = "",
    val words: String = "",
    val computer: String = "",
    @SerialName("linked_at") val linkedAt: Double = 0.0,
    @SerialName("pool_use") val poolUse: Boolean = false,
)

@Serializable
data class PoolInfo(val name: String = "", val governed: Boolean = false, val policy: String? = null)

@Serializable
data class Memory(@SerialName("used_bytes") val usedBytes: Long? = null, @SerialName("total_bytes") val totalBytes: Long? = null)

@Serializable
data class Gpu(
    val name: String = "",
    @SerialName("memory_used_bytes") val memoryUsedBytes: Long? = null,
    @SerialName("memory_total_bytes") val memoryTotalBytes: Long? = null,
    val load: Double? = null,
)

@Serializable
data class Device(
    val id: String,
    val name: String = "",
    val kind: String = "computer",
    @SerialName("this_computer") val thisComputer: Boolean = false,
    val online: Boolean = false,
    val busy: Boolean = false,
    val paused: Boolean = false,
    @SerialName("paused_because") val pausedBecause: String = "",
    val slots: Int = 0,
    val free: Int = 0,
    val queued: Int = 0,
    val room: String = "",
    val gpu: String = "",
    val serving: List<String> = emptyList(),
    val models: List<String> = emptyList(),
    val version: String = "",
    val address: String = "",
    @SerialName("last_seen") val lastSeen: Double = 0.0,
    @SerialName("pool_use") val poolUse: Boolean = false,
    // Measured load, shown when the computer knows it and left out of the screen when it does not.
    val memory: Memory? = null,
    @SerialName("cpu_load") val cpuLoad: Double? = null,
    val gpus: List<Gpu> = emptyList(),
)

@Serializable
data class Devices(val pool: PoolInfo? = null, val devices: List<Device> = emptyList(), val note: String = "")

@Serializable
data class InstalledModel(val path: String, val name: String = "", @SerialName("size_bytes") val sizeBytes: Long = 0)

@Serializable
data class RunningModel(val port: Int, val models: List<String> = emptyList())

@Serializable
data class Models(
    @SerialName("can_serve") val canServe: Boolean = false,
    val installed: List<InstalledModel> = emptyList(),
    val running: List<RunningModel> = emptyList(),
)

@Serializable
data class StartModel(val path: String, val context: Int? = null)

@Serializable
data class StopModel(val port: Int)

@Serializable
data class Job(
    val id: String,
    val name: String = "",
    val state: String = "",
    @SerialName("submitted_at") val submittedAt: Double? = null,
    @SerialName("started_at") val startedAt: Double? = null,
    @SerialName("finished_at") val finishedAt: Double? = null,
    val cancellable: Boolean? = null,
    val by: String = "",
    val returncode: Int? = null,
    @SerialName("elapsed_s") val elapsedS: Double? = null,
)

@Serializable
data class Jobs(val jobs: List<Job> = emptyList())

@Serializable
data class JobLog(val log: String = "")

/** The computer's own scheduled-job rows: only `id` is promised, so each is read field by field. */
@Serializable
data class ScheduledJobs(val available: Boolean = false, val jobs: List<JsonObject> = emptyList())

@Serializable
data class ScheduledDetail(val job: JsonObject, val log: String = "")

@Serializable
data class Project(val id: String, val name: String = "", val path: String = "", val connected: Boolean = false)

@Serializable
data class Projects(val projects: List<Project> = emptyList())

@Serializable
data class StartDelegate(val project: String, val task: String, val edit: Boolean, val model: String)

@Serializable
data class Started(val name: String = "", val said: String = "")

@Serializable
data class Run(
    val name: String,
    val state: String = "",
    val doing: String = "",
    val task: String = "",
    val model: String = "",
    val lead: String = "",
    val started: Double? = null,
    val seconds: Double? = null,
    val edit: Boolean = false,
    val answer: String = "",
)

@Serializable
data class Runs(val runs: List<Run> = emptyList())

@Serializable
data class RunPage(val run: Run, val events: List<JsonObject> = emptyList(), val next: Long = 0, val more: Boolean = false)

@Serializable
data class Said(val stopped: Boolean = false, val said: String = "")

@Serializable
data class Session(
    val id: String,
    val kind: String = "agent",
    val title: String = "",
    val agent: String = "",
    val model: String = "",
    val project: String = "",
    val device: String = "",
    @SerialName("this_computer") val thisComputer: Boolean = true,
    val parent: String? = null,
    val state: String = "idle",
    val mode: String = "none",
    val note: String = "",
    @SerialName("active_at") val activeAt: Double = 0.0,
    @SerialName("last_text") val lastText: String = "",
    val waiting: Int = 0,
)

@Serializable
data class Sessions(val sessions: List<Session> = emptyList(), val note: String = "")

@Serializable
data class OneSession(val session: Session)

@Serializable
data class Option(val label: String, val description: String = "")

@Serializable
data class Question(
    val question: String,
    val header: String = "",
    @SerialName("multiSelect") val multiSelect: Boolean = false,
    val options: List<Option> = emptyList(),
)

/** One entry of a session's history. `type` says which of the other fields mean something. */
@Serializable
data class Event(
    val id: String,
    val at: Double = 0.0,
    val type: String = "",
    val text: String = "",
    val from: String = "",
    val message: String = "",
    val ok: Boolean = true,
    val question: String = "",
    val questions: List<Question> = emptyList(),
    @SerialName("expires_at") val expiresAt: Double? = null,
    val by: String = "",
    val kind: String = "",
    /** In the inbox, the session a notice belongs to. */
    val session: String = "",
)

@Serializable
data class EventPage(val events: List<Event> = emptyList(), val next: String = "", val more: Boolean = false)

@Serializable
data class SendMessage(val text: String)

@Serializable
data class Sent(val message: String = "", val state: String = "", val mode: String = "", val note: String = "")

@Serializable
data class Answer(val answers: Map<String, String>)

@Serializable
data class InboxQuestion(
    val session: String,
    val question: String,
    val questions: List<Question> = emptyList(),
    @SerialName("expires_at") val expiresAt: Double? = null,
    @SerialName("asked_at") val askedAt: Double? = null,
)

@Serializable
data class Inbox(val questions: List<InboxQuestion> = emptyList(), val notices: List<Event> = emptyList())

@Serializable
data class PoolUse(
    val enabled: Boolean = false,
    val offering: Boolean = false,
    val model: kotlinx.serialization.json.JsonElement? = null,
    val queued: Int = 0,
)

@Serializable
data class SetPoolUse(val enabled: Boolean)

@Serializable
data class Pause(@SerialName("for") val duration: String = "", val reason: String = "")

@Serializable
data class Paused(val reached: Int = 0, val total: Int = 0)

/** The data of one `session` event of the stream. */
@Serializable
data class StreamedEvent(val session: String, val event: Event)

/** The data of one `changed` event of the stream: the lists to read again. */
@Serializable
data class Changed(val what: List<String> = emptyList())
