package app.poolhouse.core.net

import app.poolhouse.core.api.Answer
import app.poolhouse.core.api.Devices
import app.poolhouse.core.api.EventPage
import app.poolhouse.core.api.Inbox
import app.poolhouse.core.api.Job
import app.poolhouse.core.api.JobLog
import app.poolhouse.core.api.Jobs
import app.poolhouse.core.api.Me
import app.poolhouse.core.api.Models
import app.poolhouse.core.api.Pause
import app.poolhouse.core.api.Paused
import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.api.PoolUse
import app.poolhouse.core.api.RunPage
import app.poolhouse.core.api.Runs
import app.poolhouse.core.api.Said
import app.poolhouse.core.api.ScheduledJobs
import app.poolhouse.core.api.SendMessage
import app.poolhouse.core.api.Sent
import app.poolhouse.core.api.Sessions
import app.poolhouse.core.api.SetPoolUse
import app.poolhouse.core.api.StartModel
import app.poolhouse.core.api.StopModel
import app.poolhouse.core.device.ComputerLink
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import java.net.URLEncoder

/** One path segment or query value, percent-encoded: signed and sent exactly as written here. */
fun encode(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")

/** Every call of docs/phone-api.md the app makes, by name. */
interface PhoneCalls {
    suspend fun me(): Me
    suspend fun unlink()
    suspend fun devices(): Devices
    suspend fun pausePool(duration: String, reason: String): Paused
    suspend fun resumePool(): Paused
    suspend fun models(): Models
    suspend fun startModel(path: String, context: Int?)
    suspend fun stopModel(port: Int)
    suspend fun jobs(): Jobs
    suspend fun job(id: String): Job
    suspend fun jobLog(id: String): JobLog
    suspend fun stopJob(id: String)
    suspend fun scheduled(): ScheduledJobs
    suspend fun cancelScheduled(id: String)
    suspend fun delegates(): Runs
    suspend fun delegate(name: String, after: Long): RunPage
    suspend fun stopDelegate(name: String): Said
    suspend fun sessions(): Sessions
    suspend fun events(session: String, after: String): EventPage
    suspend fun send(session: String, text: String): Sent
    suspend fun answer(session: String, question: String, answers: Map<String, String>)
    suspend fun stopSession(session: String): Said
    suspend fun inbox(): Inbox
    suspend fun poolUse(): PoolUse
    suspend fun setPoolUse(enabled: Boolean): PoolUse
    fun stream(): Flow<StreamItem>
}

class PhoneApi(private val client: PhoneClient) : PhoneCalls, ComputerLink {
    private val anything = JsonElement.serializer()
    private val nothing = JsonObject(emptyMap())

    private suspend fun postEmpty(path: String): JsonElement = client.call("POST", path, nothing.toString())

    override suspend fun me() = client.get("/me", Me.serializer())

    override suspend fun unlink() {
        client.call("DELETE", "/me")
    }

    override suspend fun devices() = client.get("/devices", Devices.serializer())

    override suspend fun pausePool(duration: String, reason: String) =
        client.post("/pool/pause", Pause.serializer(), Pause(duration, reason), Paused.serializer())

    override suspend fun resumePool() =
        PhoneJson.decodeFromJsonElement(Paused.serializer(), postEmpty("/pool/resume"))

    override suspend fun models() = client.get("/models", Models.serializer())

    override suspend fun startModel(path: String, context: Int?) {
        // Loading a model takes as long as it takes: the answer comes when it is running.
        client.call("POST", "/models/start", PhoneJson.encodeToString(StartModel.serializer(), StartModel(path, context)), readMs = 600_000)
    }

    override suspend fun stopModel(port: Int) {
        client.call("POST", "/models/stop", PhoneJson.encodeToString(StopModel.serializer(), StopModel(port)), readMs = 60_000)
    }

    override suspend fun jobs() = client.get("/jobs", Jobs.serializer())

    override suspend fun job(id: String) = client.get("/jobs/${encode(id)}", Job.serializer())

    override suspend fun jobLog(id: String) = client.get("/jobs/${encode(id)}/log?tail=200", JobLog.serializer())

    override suspend fun stopJob(id: String) {
        postEmpty("/jobs/${encode(id)}/stop")
    }

    override suspend fun scheduled() = client.get("/scheduled", ScheduledJobs.serializer())

    override suspend fun cancelScheduled(id: String) {
        postEmpty("/scheduled/${encode(id)}/cancel")
    }

    override suspend fun delegates() = client.get("/delegates", Runs.serializer())

    override suspend fun delegate(name: String, after: Long) = client.get("/delegates/${encode(name)}?after=$after", RunPage.serializer())

    override suspend fun stopDelegate(name: String) =
        PhoneJson.decodeFromJsonElement(Said.serializer(), postEmpty("/delegates/${encode(name)}/stop"))

    override suspend fun sessions() = client.get("/sessions", Sessions.serializer())

    override suspend fun events(session: String, after: String): EventPage {
        val query = if (after.isEmpty()) "" else "?after=${encode(after)}"
        return client.get("/sessions/${encode(session)}/events$query", EventPage.serializer())
    }

    override suspend fun send(session: String, text: String) =
        client.post("/sessions/${encode(session)}/messages", SendMessage.serializer(), SendMessage(text), Sent.serializer())

    override suspend fun answer(session: String, question: String, answers: Map<String, String>) {
        client.post("/sessions/${encode(session)}/questions/${encode(question)}/answer", Answer.serializer(), Answer(answers), anything)
    }

    override suspend fun stopSession(session: String) =
        PhoneJson.decodeFromJsonElement(Said.serializer(), postEmpty("/sessions/${encode(session)}/stop"))

    override suspend fun inbox() = client.get("/inbox", Inbox.serializer())

    override suspend fun poolUse() = client.get("/pool-use", app.poolhouse.core.api.PoolUse.serializer())

    override suspend fun setPoolUse(enabled: Boolean) =
        client.post("/pool-use", SetPoolUse.serializer(), SetPoolUse(enabled), PoolUse.serializer())

    override fun stream() = client.stream()

    override suspend fun call(op: String, body: String?, readTimeoutMs: Long): String {
        require(Regex("[A-Za-z0-9_./-]+(\\?[A-Za-z0-9_=&.%-]*)?").matches(op) && ".." !in op) { "Not an operation of the phone API." }
        return client.call(if (body == null) "GET" else "POST", "/$op", body, readTimeoutMs.coerceIn(1_000, 900_000).toInt()).toString()
    }
}
