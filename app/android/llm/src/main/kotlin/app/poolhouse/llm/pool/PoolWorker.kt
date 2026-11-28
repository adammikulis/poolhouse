/** The one switch that lends the phone's model to the pool, and the work it does while on. */
package app.poolhouse.llm.pool

import app.poolhouse.core.device.ComputerLink
import app.poolhouse.core.device.PhoneWorker
import app.poolhouse.core.device.WorkerPhase
import app.poolhouse.core.device.WorkerState
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationResult
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull

/** The phone's own record of the switch: its position, and whether the computer has yet to hear of it. */
interface SwitchStore {
    var enabled: Boolean
    var unsent: Boolean
}

/** What the phone lends. */
interface Lender {
    /** The model the pool may ask, or null when none is downloaded. */
    fun offered(): Offered?

    /** Answers one request on the phone's model, holding the calling thread; throws when it cannot. */
    fun answer(request: PoolRequest, cancel: CancelToken, onText: (String) -> Unit): GenerationResult
}

/** How often the worker looks, in milliseconds. */
data class Timing(
    /** At most five posts a second; the computer takes about ten. */
    val flush: Long = 200,
    /** Half the longest silence the computer allows between posts. */
    val keepAlive: Long = 30_000,
    val blockedLook: Long = 15_000,
    val firstRetry: Long = 5_000,
    val longestRetry: Long = 60_000,
)

/** Longer than the [POLL_SECONDS] a poll waits at the computer. */
private const val READ_TIMEOUT_MS = (POLL_SECONDS + 15) * 1000L

private const val TURNED_OFF = "Pool use was turned off on the phone."
const val COST = "While on, answering for the pool uses battery and warms the phone. It pauses when the phone is hot, " +
    "in battery saver, or under $LEAST_BATTERY_PERCENT% off the charger. Your own questions come first."

class PoolWorker(
    private val link: ComputerLink,
    private val store: SwitchStore,
    private val lender: Lender,
    private val condition: () -> PhoneCondition,
    private val scope: CoroutineScope,
    private val compute: CoroutineDispatcher,
    private val timing: Timing = Timing(),
    /** Told each time the switch moves, to start or stop what keeps the app running. */
    private val onSwitched: (Boolean) -> Unit = {},
) : PhoneWorker {
    private val current = MutableStateFlow(initial())
    override val state: StateFlow<WorkerState> get() = current

    private val guard = Any()
    private var loop: Job? = null
    private var serving: CancelToken? = null
    private val sending = Mutex()

    /** The linked computer's name, once it has said it. */
    @Volatile private var computer: String? = null
    private val asker: String get() = computer ?: "your computer"

    private fun initial() = if (store.enabled) say(WorkerPhase.STARTING, "Starting.") else off("Only you use this phone's model.")

    /** Takes up the work when the switch was left on; the app calls it once it can reach the computer. */
    fun resume() {
        if (store.enabled) start()
    }

    /**
     * The app reaches a computer through another link now: what was learnt and asked through the old one is dropped, and
     * the work starts again through the new one without waiting out a retry.
     */
    fun relinked() {
        synchronized(guard) {
            serving?.cancel("The phone is linked to another computer.")
            loop?.cancel()
            loop = null
            computer = null
        }
        resume()
    }

    override fun setEnabled(on: Boolean) {
        synchronized(guard) {
            if (store.enabled == on && (on == (loop?.isActive == true))) return
            store.enabled = on
            store.unsent = true
        }
        onSwitched(on)
        if (on) {
            current.value = say(WorkerPhase.STARTING, "Telling your computer.")
            start()
        } else {
            val job: Job?
            val request: CancelToken?
            synchronized(guard) {
                job = loop
                request = serving
            }
            current.value = off("Only you use this phone's model.")
            // A running request is ended and told why; an idle wait is dropped.
            if (request != null) request.cancel(TURNED_OFF) else job?.cancel()
            scope.launch { runCatching { sendSwitch() } }
        }
    }

    /** Reads the computer's position of the switch, or sends the phone's when the computer has not heard it. */
    override fun refresh() {
        scope.launch {
            runCatching {
                if (store.unsent) {
                    sendSwitch()
                } else {
                    val there = PoolProtocol.switched(link.call("pool-use", null, READ_TIMEOUT_MS))
                    if (!store.unsent && there != store.enabled) follow(there)
                }
            }
        }
    }

    private fun follow(on: Boolean) {
        store.enabled = on
        onSwitched(on)
        if (on) {
            current.value = say(WorkerPhase.STARTING, "Turned on from your computer.")
            start()
        } else {
            current.value = off("Turned off from your computer.")
            val request = synchronized(guard) { serving }
            request?.cancel(TURNED_OFF)
        }
    }

    private suspend fun sendSwitch() = sending.withLock {
        while (store.unsent) {
            val position = store.enabled
            send("pool-use", PoolProtocol.switchBody(position))
            if (store.enabled == position) store.unsent = false
        }
    }

    private fun start() {
        synchronized(guard) {
            val before = loop
            if (before?.isActive == true && !before.isCancelled) return
            loop = scope.launch { work() }
        }
    }

    private suspend fun work() {
        var retry = timing.firstRetry
        while (store.enabled) {
            try {
                sendSwitch()
                if (!store.enabled) break
                val now = gate(condition())
                val model = lender.offered()
                if (model == null) {
                    show(say(WorkerPhase.READY, "On, and taking no work.", blocked = "Download a model to lend."))
                    delay(timing.blockedLook)
                    continue
                }
                if (computer == null) computer = PoolProtocol.computer(link.call("me", null, READ_TIMEOUT_MS))
                val blocked = now.reason.takeUnless { now.accept }
                show(
                    if (blocked == null) {
                        say(WorkerPhase.READY, "Waiting for work from $asker.", model.name)
                    } else {
                        say(WorkerPhase.READY, "On, and taking no work.", model.name, blocked)
                    },
                )
                // A blocked phone still polls, so the computer shows why; the computer sends it nothing.
                val poll = PoolProtocol.poll(send("model/next", PoolProtocol.pollBody(model, blocked)))
                retry = timing.firstRetry
                if (!poll.enabled) {
                    if (store.enabled && !store.unsent) follow(false)
                    break
                }
                poll.refusal?.let { (id, reason) -> post(id, PoolProtocol.error(0, reason)) }
                poll.request?.let { if (blocked == null) serve(it, model) else post(it.id, PoolProtocol.error(0, blocked)) }
                if (blocked != null) delay(timing.blockedLook)
            } catch (stopped: CancellationException) {
                throw stopped
            } catch (failure: Exception) {
                show(say(WorkerPhase.FAILED, "Your computer is not answering. The phone keeps trying."))
                delay(retry)
                retry = (retry * 2).coerceAtMost(timing.longestRetry)
            }
        }
    }

    private suspend fun post(id: String, body: String): Boolean =
        PoolProtocol.carryOn(send("model/requests/$id", body))

    private suspend fun send(operation: String, body: String): String = link.call(operation, body, READ_TIMEOUT_MS)

    private suspend fun serve(request: PoolRequest, model: Offered) {
        val cancel = CancelToken()
        synchronized(guard) { serving = cancel }
        val written = StringBuilder()
        fun take(): String = synchronized(written) { written.toString().also { written.setLength(0) } }
        try {
            if (!store.enabled) cancel.cancel(TURNED_OFF)
            show(say(WorkerPhase.BUSY, "Answering a request from $asker.", model.name))
            var seq = 0
            // The first post says the phone has the request, before a model that is not loaded yet is read in.
            if (!post(request.id, PoolProtocol.part(seq++, ""))) cancel.cancel("The computer stopped asking.")
            val reply = scope.async(compute) {
                runCatching {
                    check(!cancel.cancelled) { cancel.reason ?: TURNED_OFF }
                    lender.answer(request, cancel) { synchronized(written) { written.append(it) } }
                }
            }
            var quiet = 0L
            while (!reply.isCompleted) {
                withTimeoutOrNull(timing.flush) { reply.join() }
                if (!store.enabled) cancel.cancel(TURNED_OFF)
                gate(condition()).let { if (it.stop) cancel.cancel(it.reason ?: TURNED_OFF) }
                if (reply.isCompleted || cancel.cancelled) continue
                quiet += timing.flush
                val text = take()
                if (text.isEmpty() && quiet < timing.keepAlive) continue
                quiet = 0
                for (piece in PoolProtocol.pieces(text).ifEmpty { listOf("") }) {
                    if (!post(request.id, PoolProtocol.part(seq++, piece))) cancel.cancel("The computer stopped asking.")
                }
            }
            val outcome = reply.await()
            val result = outcome.getOrNull()
            if (result == null || result.finish == Finish.CANCELLED || cancel.cancelled) {
                val reason = cancel.reason ?: outcome.exceptionOrNull()?.message ?: "The phone could not answer."
                post(request.id, PoolProtocol.error(seq, reason))
            } else {
                val pieces = PoolProtocol.pieces(take())
                for (piece in pieces.dropLast(1)) post(request.id, PoolProtocol.part(seq++, piece))
                post(request.id, PoolProtocol.done(seq, pieces.lastOrNull() ?: "", result))
            }
        } finally {
            cancel.cancel("The phone stopped.")
            // What was written is dropped with the request: nothing of it is kept, stored or logged.
            take()
            synchronized(guard) { serving = null }
        }
    }

    private fun show(next: WorkerState) {
        if (store.enabled) current.value = next
    }

    private fun say(phase: WorkerPhase, detail: String, model: String? = null, blocked: String? = null) =
        WorkerState(phase, detail, model, blocked, COST)

    private fun off(detail: String) = WorkerState(WorkerPhase.OFF, detail, null, null, COST)
}
