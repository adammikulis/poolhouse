/** The one model the phone holds and the one reply it computes at a time. */
package app.poolhouse.llm.engine

import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/** Who asks for the phone's compute: the person holding it, or the pool. */
enum class Caller { PERSON, POOL }

/** The phone cannot take the pool's request now; the message says why. */
class Busy(message: String) : RuntimeException(message)

/**
 * Holds at most one loaded model and runs one reply at a time. The person comes first: their request stops a pool reply
 * that is running, and a pool request made while anything runs or waits is refused with [Busy].
 */
class ModelHost<K : Any> {
    private val lock = ReentrantLock()
    private val freed = lock.newCondition()
    private var running: CancelToken? = null
    private var runningFor: Caller? = null
    private var peopleWaiting = 0
    private var held: Pair<K, LoadedModel>? = null

    val busyFor: Caller? get() = lock.withLock { runningFor }

    /** The key of the loaded model, or null when none is held. */
    val loadedKey: K? get() = lock.withLock { held?.first }

    /**
     * Runs `work` on the model of `key`, loading it with `load` (and closing any other) first. Blocks until the phone is
     * free when `caller` is the person.
     */
    fun <T> use(caller: Caller, key: K, cancel: CancelToken, load: () -> LoadedModel, work: (LoadedModel) -> T): T {
        take(caller, cancel)
        try {
            val current = held
            val model = if (current != null && current.first == key) {
                current.second
            } else {
                unload()
                load().also { held = key to it }
            }
            return work(model)
        } finally {
            give()
        }
    }

    /** Stops a pool reply that is running; the person's own reply goes on. */
    fun stopPool(why: String) {
        lock.withLock { if (runningFor == Caller.POOL) running else null }?.cancel(why)
    }

    /** Frees the model's memory once nothing runs. */
    fun release() {
        take(Caller.PERSON, CancelToken())
        try {
            unload()
        } finally {
            give()
        }
    }

    private fun unload() {
        val current = held ?: return
        held = null
        current.second.close()
    }

    private fun take(caller: Caller, cancel: CancelToken) {
        var stop: CancelToken? = null
        lock.withLock {
            if (caller == Caller.POOL) {
                if (running != null || peopleWaiting > 0) throw Busy("The phone is answering something else.")
            } else {
                peopleWaiting++
                if (runningFor == Caller.POOL) stop = running
            }
        }
        stop?.cancel("The person holding the phone asked it something.")
        lock.withLock {
            if (caller == Caller.PERSON) {
                while (running != null) freed.await()
                peopleWaiting--
            } else if (running != null || peopleWaiting > 0) {
                throw Busy("The phone is answering something else.")
            }
            running = cancel
            runningFor = caller
        }
    }

    private fun give() = lock.withLock {
        running = null
        runningFor = null
        freed.signalAll()
    }
}
