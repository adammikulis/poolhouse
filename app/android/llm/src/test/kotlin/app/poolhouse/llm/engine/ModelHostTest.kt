/** One reply at a time, the person first, one model held. The model here is a marked stand-in: only the queue is under test. */
package app.poolhouse.llm.engine

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

private class NotAModel(val name: String, val closed: MutableList<String>) : LoadedModel {
    override val accelerator = Accelerator.CPU
    override val contextSize = 1
    override val acceptsImages = false

    override fun generate(turns: List<ChatTurn>, options: GenerationOptions, cancel: CancelToken, onText: (String) -> Unit) =
        throw UnsupportedOperationException("CANNED-NOT-A-MODEL")

    override fun close() {
        closed += name
    }
}

class ModelHostTest {
    private val closed = mutableListOf<String>()

    @Test fun theSameModelStaysLoadedAndAnotherReplacesIt() {
        val host = ModelHost<String>()
        var loads = 0
        fun use(key: String) = host.use(Caller.PERSON, key, CancelToken(), { loads++; NotAModel(key, closed) }) {}
        use("a")
        use("a")
        use("b")
        assertEquals(2, loads)
        assertEquals(listOf("a"), closed)
        assertEquals("b", host.loadedKey)
        host.release()
        assertEquals(listOf("a", "b"), closed)
    }

    @Test fun thePersonStopsAPoolReplyAndTheRequestWaitsForIt() {
        val host = ModelHost<String>()
        val pool = CancelToken()
        val started = CountDownLatch(1)
        val stopped = CountDownLatch(1)
        val order = mutableListOf<String>()
        pool.onCancel { stopped.countDown() }
        val worker = thread {
            host.use(Caller.POOL, "a", pool, { NotAModel("a", closed) }) {
                started.countDown()
                assertTrue(stopped.await(5, TimeUnit.SECONDS))
                synchronized(order) { order += "pool ended" }
            }
        }
        assertTrue(started.await(5, TimeUnit.SECONDS))
        host.use(Caller.PERSON, "a", CancelToken(), { NotAModel("a", closed) }) { synchronized(order) { order += "person" } }
        worker.join(5000)
        assertEquals(listOf("pool ended", "person"), order)
        assertTrue(pool.cancelled)
    }

    @Test fun thePoolIsRefusedWhileThePersonIsAnswered() {
        val host = ModelHost<String>()
        host.use(Caller.PERSON, "a", CancelToken(), { NotAModel("a", closed) }) {
            val other = thread {
                assertThrows(Busy::class.java) { host.use(Caller.POOL, "a", CancelToken(), { NotAModel("a", closed) }) {} }
            }
            other.join(5000)
        }
        host.use(Caller.POOL, "a", CancelToken(), { NotAModel("a", closed) }) {}
    }

    @Test fun aStopForThePoolLeavesThePersonAlone() {
        val host = ModelHost<String>()
        val person = CancelToken()
        host.use(Caller.PERSON, "a", person, { NotAModel("a", closed) }) { host.stopPool("hot") }
        assertEquals(null, person.reason)
    }
}
