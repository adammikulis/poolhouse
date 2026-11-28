package app.poolhouse.core.store

import app.poolhouse.core.api.Changed
import app.poolhouse.core.api.Devices
import app.poolhouse.core.api.Hello
import app.poolhouse.core.api.Inbox
import app.poolhouse.core.api.Jobs
import app.poolhouse.core.api.Me
import app.poolhouse.core.api.Models
import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.api.PoolUse
import app.poolhouse.core.api.Projects
import app.poolhouse.core.api.Runs
import app.poolhouse.core.api.ScheduledJobs
import app.poolhouse.core.api.Sessions
import app.poolhouse.core.api.StartDelegate
import app.poolhouse.core.api.StreamedEvent
import app.poolhouse.core.net.PhoneCalls
import app.poolhouse.core.net.PhoneError
import app.poolhouse.core.net.StreamItem
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicLong

sealed interface Connection {
    data object Connecting : Connection

    data object Online : Connection

    data class Offline(val why: String) : Connection

    /** The computer no longer knows the phone: nothing more is tried. */
    data class Unlinked(val why: String) : Connection
}

/** One list of the computer as last read: the last good value stays while a later read fails, and says why. */
data class Loaded<T>(val value: T? = null, val error: String = "", val loading: Boolean = false)

data class PoolState(
    val connection: Connection = Connection.Connecting,
    /** The computer is the sample server the app is checked against: everything shown is canned and says so. */
    val canned: Boolean = false,
    val computer: String = "",
    val sessions: Loaded<Sessions> = Loaded(),
    val devices: Loaded<Devices> = Loaded(),
    val models: Loaded<Models> = Loaded(),
    val jobs: Loaded<Jobs> = Loaded(),
    val scheduled: Loaded<ScheduledJobs> = Loaded(),
    val runs: Loaded<Runs> = Loaded(),
    val inbox: Loaded<Inbox> = Loaded(),
    val poolUse: Loaded<PoolUse> = Loaded(),
    val me: Loaded<Me> = Loaded(),
    val projects: Loaded<Projects> = Loaded(),
    val transcripts: Map<String, Transcript> = emptyMap(),
    /** When the person last saw each session, by its `active_at`. */
    val seen: Map<String, Double> = emptyMap(),
    /** The actions in hand, by key, so their buttons show it. */
    val busy: Set<String> = emptySet(),
    /** What the last action said, shown once. */
    val notice: String = "",
)

/** Where the phone keeps what the person has seen. */
interface SeenStore {
    fun load(): Map<String, Double>

    fun save(seen: Map<String, Double>)
}

private val LISTS = listOf("sessions", "inbox", "devices", "delegates", "jobs", "scheduled", "models", "pool-use", "me")

/** The longest message, and the longest task, the computer accepts. */
const val MESSAGE_LIMIT = 8_000

/**
 * The app's view of the linked computer: it reads the lists, follows the live stream while the app is on screen, and carries out what
 * the person asks. Every state the screens show comes from the computer's answers, the person's own drafts, or says it is missing.
 */
class PoolStore(
    private val calls: PhoneCalls,
    private val hello: suspend () -> Hello,
    private val scope: CoroutineScope,
    private val seenStore: SeenStore,
    computer: String = "",
    private val clock: () -> Long = System::currentTimeMillis,
    /** Run when the computer says the pool-use switch changed, so the on-device model reads it again. */
    private val onPoolUseChanged: () -> Unit = {},
) {
    private val firstRun = seenStore.load().isEmpty()
    private val mutable = MutableStateFlow(PoolState(computer = computer, seen = seenStore.load()))
    val state: StateFlow<PoolState> = mutable.asStateFlow()

    private var follower: Job? = null
    private val open = LinkedHashSet<String>()
    private val reading = HashMap<String, Job>()
    private val dirty = HashSet<String>()
    private val drafts = AtomicLong()
    private var baseline = firstRun

    private fun change(edit: (PoolState) -> PoolState) = mutable.update(edit)

    /** Connects and follows the computer until [pause]. Called when the app comes on screen. */
    fun resume() {
        if (follower?.isActive == true || state.value.connection is Connection.Unlinked) return
        follower = scope.launch { follow() }
    }

    fun pause() {
        follower?.cancel()
        follower = null
    }

    private suspend fun follow() {
        var wait = 1_000L
        while (true) {
            try {
                val said = hello()
                change { it.copy(canned = said.canned, computer = said.computer.ifEmpty { it.computer }) }
                val started = clock()
                calls.stream().collect { item ->
                    when (item) {
                        StreamItem.Opened -> {
                            wait = 1_000L
                            change { it.copy(connection = Connection.Online) }
                            refresh()
                        }
                        is StreamItem.Message -> take(item)
                    }
                }
                // The computer closes a stream after ten minutes: connect again at once. One closed at once is not asked in a loop,
                // since every connection reads all the lists again.
                delay(if (clock() - started < 10_000) 3_000 else 250)
            } catch (error: CancellationException) {
                throw error
            } catch (error: PhoneError.Unlinked) {
                unlinked(error)
                return
            } catch (error: Exception) {
                change { it.copy(connection = Connection.Offline(error.message ?: "The computer cannot be reached.")) }
                delay(wait)
                wait = (wait * 2).coerceAtMost(15_000L)
            }
        }
    }

    private fun unlinked(error: PhoneError) = change { it.copy(connection = Connection.Unlinked(error.message ?: "")) }

    private fun take(message: StreamItem.Message) {
        when (message.event) {
            "session" -> {
                val streamed = runCatching { PhoneJson.decodeFromString(StreamedEvent.serializer(), message.data) }.getOrNull() ?: return
                val id = streamed.session
                change { s ->
                    val held = s.transcripts[id] ?: return@change s
                    s.copy(transcripts = s.transcripts + (id to held.add(listOf(streamed.event))))
                }
                if (id in open) markSeen(id, streamed.event.at)
            }
            "changed" -> {
                val changed = runCatching { PhoneJson.decodeFromString(Changed.serializer(), message.data) }.getOrNull() ?: return
                for (what in changed.what) read(what)
                if ("sessions" in changed.what) open.toList().forEach(::readEvents)
                // The switch is kept on the computer and shown on the devices list, so either change reads it again.
                if ("devices" in changed.what || "pool-use" in changed.what) {
                    read("pool-use")
                    onPoolUseChanged()
                }
            }
        }
    }

    /** Reads every list and every open conversation again. */
    fun refresh() {
        LISTS.forEach(::read)
        open.toList().forEach(::readEvents)
    }

    /** Reads one list, once more after the read in hand when it changed meanwhile. */
    fun read(what: String) {
        if (reading[what]?.isActive == true) {
            dirty.add(what)
            return
        }
        reading[what] = scope.launch {
            do {
                dirty.remove(what)
                readNow(what)
            } while (what in dirty)
        }
    }

    private suspend fun readNow(what: String) {
        when (what) {
            "sessions" -> list({ it.sessions }, { s, v -> s.copy(sessions = v) }) { calls.sessions().also(::noteSessions) }
            "inbox" -> list({ it.inbox }, { s, v -> s.copy(inbox = v) }) { calls.inbox() }
            "devices" -> list({ it.devices }, { s, v -> s.copy(devices = v) }) { calls.devices() }
            "delegates" -> list({ it.runs }, { s, v -> s.copy(runs = v) }) { calls.delegates() }
            "jobs" -> list({ it.jobs }, { s, v -> s.copy(jobs = v) }) { calls.jobs() }
            "scheduled" -> list({ it.scheduled }, { s, v -> s.copy(scheduled = v) }) { calls.scheduled() }
            "models" -> list({ it.models }, { s, v -> s.copy(models = v) }) { calls.models() }
            "pool-use" -> list({ it.poolUse }, { s, v -> s.copy(poolUse = v) }) { calls.poolUse() }
            "me" -> list({ it.me }, { s, v -> s.copy(me = v) }) { calls.me() }
            "projects" -> list({ it.projects }, { s, v -> s.copy(projects = v) }) { calls.projects() }
        }
    }

    private suspend fun <T> list(get: (PoolState) -> Loaded<T>, put: (PoolState, Loaded<T>) -> PoolState, read: suspend () -> T) {
        change { put(it, get(it).copy(loading = true)) }
        try {
            val value = read()
            change { put(it, Loaded(value)) }
        } catch (error: CancellationException) {
            throw error
        } catch (error: PhoneError.Unlinked) {
            unlinked(error)
        } catch (error: Exception) {
            change { put(it, get(it).copy(error = error.message ?: "Not read.", loading = false)) }
        }
    }

    private fun noteSessions(sessions: Sessions) {
        val seen = HashMap(state.value.seen)
        // The first list a new link shows is not all unread: it is where reading starts.
        for (session in sessions.sessions) {
            if (session.id !in seen) seen[session.id] = if (baseline) session.activeAt else 0.0
            if (session.id in open) seen[session.id] = maxOf(seen.getValue(session.id), session.activeAt)
        }
        baseline = false
        val kept = seen.filterKeys { id -> sessions.sessions.any { it.id == id } }
        change { it.copy(seen = kept) }
        seenStore.save(kept)
    }

    private fun markSeen(id: String, at: Double = 0.0) {
        val active = state.value.sessions.value?.sessions?.firstOrNull { it.id == id }?.activeAt ?: 0.0
        val mark = maxOf(at, active, state.value.seen[id] ?: 0.0)
        change { it.copy(seen = it.seen + (id to mark)) }
        seenStore.save(state.value.seen)
    }

    fun openSession(id: String) {
        open.add(id)
        change { if (id in it.transcripts) it else it.copy(transcripts = it.transcripts + (id to Transcript())) }
        markSeen(id)
        readEvents(id)
    }

    fun closeSession(id: String) {
        markSeen(id)
        open.remove(id)
    }

    private fun editTranscript(id: String, edit: (Transcript) -> Transcript) = change { s ->
        s.copy(transcripts = s.transcripts + (id to edit(s.transcripts[id] ?: Transcript())))
    }

    private fun readEvents(id: String) {
        val key = "events:$id"
        if (reading[key]?.isActive == true) {
            dirty.add(key)
            return
        }
        reading[key] = scope.launch {
            do {
                dirty.remove(key)
                try {
                    var pages = 0
                    do {
                        val page = calls.events(id, state.value.transcripts[id]?.next ?: "")
                        editTranscript(id) { it.page(page.events, page.next) }
                    } while (page.more && ++pages < 50)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: PhoneError.Unlinked) {
                    unlinked(error)
                } catch (error: Exception) {
                    editTranscript(id) { it.failed(error.message ?: "The history was not read.") }
                }
            } while (key in dirty)
        }
    }

    fun send(id: String, text: String) {
        val body = text.trim()
        if (body.isEmpty()) return
        deliver(id, Draft("local-${drafts.incrementAndGet()}", body, clock() / 1000.0))
    }

    fun retry(id: String, localId: String) {
        val draft = state.value.transcripts[id]?.drafts?.firstOrNull { it.localId == localId } ?: return
        deliver(id, draft.copy(failed = "", message = "", at = clock() / 1000.0))
    }

    fun discard(id: String, localId: String) = editTranscript(id) { it.withoutDraft(localId) }

    private fun deliver(id: String, draft: Draft) {
        editTranscript(id) { it.withDraft(draft) }
        scope.launch {
            try {
                require(draft.text.length <= MESSAGE_LIMIT) { "A message is at most $MESSAGE_LIMIT characters." }
                val sent = calls.send(id, draft.text)
                editTranscript(id) { it.withDraft(draft.copy(message = sent.message, mode = sent.mode, note = sent.note)) }
                readEvents(id)
            } catch (error: CancellationException) {
                throw error
            } catch (error: PhoneError.Unlinked) {
                unlinked(error)
            } catch (error: Exception) {
                // The text stays, with why it was not sent: it is never sent again without being asked.
                editTranscript(id) { it.withDraft(draft.copy(failed = error.message ?: "Not sent.")) }
            }
        }
    }

    /** Carries out one action of the person: [key] marks it busy, the computer's own words are shown, and the lists it changed are read. */
    private fun act(key: String, vararg reads: String, action: suspend () -> String) {
        if (key in state.value.busy) return
        change { it.copy(busy = it.busy + key) }
        scope.launch {
            try {
                val said = action()
                change { it.copy(notice = said) }
            } catch (error: CancellationException) {
                throw error
            } catch (error: PhoneError.Unlinked) {
                unlinked(error)
            } catch (error: Exception) {
                change { it.copy(notice = error.message ?: "That did not work.") }
            } finally {
                change { it.copy(busy = it.busy - key) }
                reads.forEach(::read)
            }
        }
    }

    fun clearNotice() = change { it.copy(notice = "") }

    /** Starts a local delegate on a project of the computer; [started] gets its name once it exists. */
    fun startDelegate(project: String, task: String, edit: Boolean, model: String, started: (String) -> Unit) =
        act("start", "delegates", "sessions") {
            require(task.isNotBlank() && task.length <= MESSAGE_LIMIT) { "A task is 1 to $MESSAGE_LIMIT characters." }
            val said = calls.startDelegate(StartDelegate(project, task.trim(), edit, model.trim()))
            if (said.name.isNotEmpty()) started(said.name)
            said.said.ifEmpty { "${said.name} started." }
        }

    fun answer(session: String, question: String, answers: Map<String, String>) {
        act("answer:$question", "inbox", "sessions") {
            calls.answer(session, question, answers)
            if (session in open) readEvents(session)
            "Answer sent."
        }
    }

    fun stopSession(id: String) = act("stop:$id", "sessions", "delegates") { calls.stopSession(id).said.ifEmpty { "Stop sent." } }

    fun stopDelegate(name: String) = act("stop:$name", "delegates", "sessions") { calls.stopDelegate(name).said.ifEmpty { "Stop sent." } }

    fun stopJob(id: String) = act("stop:$id", "jobs") {
        calls.stopJob(id)
        "Stop sent."
    }

    fun cancelScheduled(id: String) = act("stop:$id", "scheduled") {
        calls.cancelScheduled(id)
        "Scheduled job cancelled."
    }

    fun startModel(path: String, name: String, context: Int?) = act("model:$path", "models", "devices") {
        calls.startModel(path, context)
        "$name is serving."
    }

    fun stopModel(port: Int) = act("model:$port", "models", "devices") {
        calls.stopModel(port)
        "Model stopped."
    }

    fun pausePool(duration: String) = act("pool", "devices") {
        val said = calls.pausePool(duration, "paused from the phone")
        "Paused ${said.reached} of ${said.total} computers."
    }

    fun resumePool() = act("pool", "devices") {
        val said = calls.resumePool()
        "Resumed ${said.reached} of ${said.total} computers."
    }

    /** The switch on the computer, for a build with no on-device model: such a phone can only be switched off. */
    fun endPoolUse() = act("pool-use", "pool-use", "devices") {
        val said = calls.setPoolUse(false)
        change { it.copy(poolUse = Loaded(said)) }
        if (said.enabled) "The computer still says the pool may use this phone." else "This phone only directs the computer."
    }
}
