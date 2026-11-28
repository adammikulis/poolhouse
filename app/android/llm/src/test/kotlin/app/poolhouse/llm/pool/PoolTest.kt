/**
 * The switch and the pool work, against a computer that follows docs/phone-api.md section 5 in memory. No model is under
 * test: what answers is marked CANNED-NOT-A-MODEL and every reply of it begins with the marker.
 */
package app.poolhouse.llm.pool

import app.poolhouse.core.device.ComputerLink
import app.poolhouse.core.device.WorkerPhase
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationResult
import app.poolhouse.llm.engine.GenerationStats
import app.poolhouse.llm.engine.Role
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

private const val MARK = "[CANNED RESPONSE, NOT A MODEL]"
private val CANNED = Offered("CANNED-NOT-A-MODEL", "CANNED-NOT-A-MODEL", "litert-lm", 16_384)
private const val SECRET = "the-source-of-another-computer"

private fun parse(text: String) = Json.parseToJsonElement(text).jsonObject
private fun JsonObject.text(key: String) = this[key]?.jsonPrimitive?.content

private class Computer : ComputerLink {
    val calls = CopyOnWriteArrayList<Pair<String, String?>>()
    val requests = Channel<String>(Channel.UNLIMITED)
    @Volatile var enabled = false
    @Volatile var away = false
    @Volatile var continues = true

    override suspend fun call(op: String, body: String?, readTimeoutMs: Long): String {
        calls += op to body
        if (away) throw java.io.IOException("away")
        return when {
            op == "me" -> """{"computer":"Studio"}"""
            op == "pool-use" -> {
                if (body != null) enabled = parse(body).text("enabled").toBoolean()
                """{"enabled":$enabled}"""
            }
            op == "model/next" -> {
                if (!enabled) return """{"request":null,"enabled":false}"""
                val request = withTimeout(60_000) { requests.receive() }
                """{"request":$request,"enabled":$enabled}"""
            }
            else -> """{"continue":$continues}"""
        }
    }

    fun answers(id: String) = calls.filter { it.first == "model/requests/$id" }.map { parse(it.second!!) }
}

private class Switch(override var enabled: Boolean = false, override var unsent: Boolean = false) : SwitchStore

private class CannedLender : Lender {
    @Volatile var model: Offered? = CANNED
    val release = CountDownLatch(1)
    val started = CountDownLatch(1)
    @Volatile var held = false
    @Volatile var seen: PoolRequest? = null

    override fun offered() = model

    override fun answer(request: PoolRequest, cancel: CancelToken, onText: (String) -> Unit): GenerationResult {
        seen = request
        onText("$MARK first ")
        started.countDown()
        if (held) {
            cancel.onCancel { release.countDown() }
            release.await(20, TimeUnit.SECONDS)
        }
        if (!cancel.cancelled) onText("second")
        val finish = if (cancel.cancelled) Finish.CANCELLED else Finish.STOP
        return GenerationResult(finish, GenerationStats(7, 3, null, null, null, 10, 16_384))
    }
}

class PoolWorkerTest {
    private val computer = Computer()
    private val store = Switch()
    private val lender = CannedLender()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val switched = CopyOnWriteArrayList<Boolean>()
    @Volatile private var condition = PhoneCondition(charging = true, batteryPercent = 80, powerSave = false, thermalStatus = 0)

    private val worker by lazy {
        PoolWorker(computer, store, lender, { condition }, scope, Dispatchers.IO, Timing(20, 100, 20, 20, 100)) { switched += it }
    }

    @After fun end() = scope.cancel()

    private fun request(id: String) =
        """{"id":"$id","model":"CANNED-NOT-A-MODEL","messages":[{"role":"user","content":"$SECRET"}],"max_tokens":64}"""

    private fun until(what: String, met: () -> Boolean) = runBlocking {
        withTimeout(10_000) { while (!met()) delay(5) }
        assertTrue(what, met())
    }

    @Test fun offUntilThePersonTurnsItOnAndThenNothingIsAsked() {
        worker.resume()
        Thread.sleep(150)
        assertEquals(WorkerPhase.OFF, worker.state.value.phase)
        assertTrue(computer.calls.isEmpty())
    }

    @Test fun onTellsTheComputerPollsAndAnswersInOrder() {
        worker.setEnabled(true)
        computer.requests.trySend(request("r-1"))
        until("answered") { computer.answers("r-1").any { it.text("done") == "true" } }
        val posts = computer.answers("r-1")
        assertEquals(posts.indices.map { it.toString() }, posts.map { it.text("seq") })
        assertEquals("$MARK first second", posts.joinToString("") { it.text("delta") ?: "" })
        assertEquals("stop", posts.last().text("finish_reason"))
        assertEquals("3", posts.last()["usage"]!!.jsonObject.text("completion_tokens"))
        val offered = parse(computer.calls.first { it.first == "model/next" }.second!!)["model"]!!.jsonObject
        assertEquals("CANNED-NOT-A-MODEL", offered.text("id"))
        assertEquals("8192", offered.text("max_output"))
        assertEquals(listOf("pool-use", "me", "model/next"), computer.calls.map { it.first }.take(3))
        assertEquals(listOf(true), switched.toList())
    }

    @Test fun thePersonSeesWhichComputerIsServed() {
        lender.held = true
        worker.setEnabled(true)
        computer.requests.trySend(request("r-1"))
        until("busy") { worker.state.value.phase == WorkerPhase.BUSY }
        assertEquals("Answering a request from Studio.", worker.state.value.detail)
        lender.release.countDown()
    }

    @Test fun aRequestIsNeitherKeptNorShownAfterItsAnswer() {
        worker.setEnabled(true)
        computer.requests.trySend(request("r-1"))
        until("answered") { computer.answers("r-1").any { it.text("done") == "true" } }
        until("ready again") { worker.state.value.phase == WorkerPhase.READY }
        assertFalse(lender.seen!!.options.keep)
        // The only record the worker keeps is the switch; no state it shows and no later call holds the request's words.
        assertFalse(worker.state.value.toString().contains(SECRET))
        assertFalse(worker.state.value.toString().contains(MARK))
        assertTrue(computer.calls.none { it.second?.contains(SECRET) == true })
    }

    @Test fun turningOffEndsTheRunningRequestAtOnceAndSaysWhy() {
        lender.held = true
        worker.setEnabled(true)
        computer.requests.trySend(request("r-1"))
        assertTrue(lender.started.await(10, TimeUnit.SECONDS))
        worker.setEnabled(false)
        assertEquals(WorkerPhase.OFF, worker.state.value.phase)
        until("told") { computer.answers("r-1").any { it.text("error") != null } }
        assertEquals("Pool use was turned off on the phone.", computer.answers("r-1").last().text("error"))
        assertTrue(computer.answers("r-1").none { it.text("done") == "true" })
        until("computer told") { !computer.enabled }
        assertEquals(listOf(true, false), switched.toList())
        val polls = computer.calls.count { it.first == "model/next" }
        Thread.sleep(150)
        assertEquals(polls, computer.calls.count { it.first == "model/next" })
    }

    @Test fun offHoldsWhileTheComputerIsAwayAndReachesItLater() {
        worker.setEnabled(true)
        until("waiting") { computer.calls.any { it.first == "model/next" } }
        computer.away = true
        worker.setEnabled(false)
        assertEquals(WorkerPhase.OFF, worker.state.value.phase)
        assertTrue(store.unsent)
        computer.away = false
        computer.enabled = true
        worker.refresh()
        until("sent") { !store.unsent }
        assertFalse(computer.enabled)
        assertFalse(store.enabled)
    }

    @Test fun theComputerTurningItOffStopsThePhone() {
        store.enabled = true
        computer.enabled = false
        worker.resume()
        until("off") { worker.state.value.phase == WorkerPhase.OFF }
        assertFalse(store.enabled)
        assertEquals(listOf(false), switched.toList())
    }

    @Test fun theComputerTurningItOnIsReadOnRefresh() {
        computer.enabled = true
        worker.refresh()
        until("on") { store.enabled && computer.calls.any { it.first == "model/next" } }
        assertEquals(listOf(true), switched.toList())
    }

    @Test fun aHotPhoneTakesNoWorkAndTellsTheComputerWhy() {
        condition = condition.copy(thermalStatus = THERMAL_MODERATE)
        worker.setEnabled(true)
        until("told") { computer.calls.any { it.first == "model/next" } }
        val poll = parse(computer.calls.first { it.first == "model/next" }.second!!)
        assertEquals("The phone is warm; it waits until it cools.", poll.text("blocked"))
        assertEquals("0", poll.text("wait_s"))
        assertEquals("The phone is warm; it waits until it cools.", worker.state.value.blocked)
        computer.requests.trySend(request("r-1"))
        until("refused") { computer.answers("r-1").any { it.text("error") != null } }
        assertNull(lender.seen)
    }

    @Test fun heatDuringAReplyEndsItWithTheReason() {
        lender.held = true
        worker.setEnabled(true)
        computer.requests.trySend(request("r-1"))
        assertTrue(lender.started.await(10, TimeUnit.SECONDS))
        condition = condition.copy(thermalStatus = THERMAL_SEVERE)
        until("told") { computer.answers("r-1").any { it.text("error") != null } }
        assertEquals("The phone is too hot.", computer.answers("r-1").last().text("error"))
    }

    @Test fun theComputerSayingStopEndsTheReply() {
        lender.held = true
        computer.continues = false
        worker.setEnabled(true)
        computer.requests.trySend(request("r-1"))
        until("told") { computer.answers("r-1").any { it.text("error") != null } }
    }

    @Test fun withNoModelTheWorkerOffersNothing() {
        lender.model = null
        worker.setEnabled(true)
        until("blocked") { worker.state.value.blocked == "Download a model to lend." }
        assertTrue(computer.calls.none { it.first == "model/next" })
        assertNull(lender.seen)
    }
}

class PoolProtocolTest {
    @Test fun aConversationIsReadInOrderAndNotKept() {
        val poll = PoolProtocol.poll(
            """{"enabled":true,"request":{"id":"r-2","messages":[{"role":"system","content":"s"},
               {"role":"user","content":"u"},{"role":"assistant","content":"a"},{"role":"user","content":"v"}],"max_tokens":9}}""",
        )
        val request = poll.request!!
        assertEquals(listOf(Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER), request.turns.map { it.role })
        assertEquals(9, request.options.maxTokens)
        assertFalse(request.options.keep)
    }

    @Test fun toolsAndToolMessagesAreRefused() {
        for (body in listOf(
            """"messages":[{"role":"user","content":"u"}],"tools":[{"type":"function"}]""",
            """"messages":[{"role":"tool","content":"done"}]""",
        )) {
            val poll = PoolProtocol.poll("""{"enabled":true,"request":{"id":"r-4",$body}}""")
            assertNull(poll.request)
            assertEquals("r-4", poll.refusal!!.first)
        }
    }

    @Test fun anIdThatWouldChangeThePathIsNotUsed() {
        assertThrows(IllegalArgumentException::class.java) {
            PoolProtocol.poll("""{"enabled":true,"request":{"id":"../pool-use","messages":[{"role":"user","content":"u"}]}}""")
        }
    }

    @Test fun aRequestThePhoneCannotReadIsRefusedByItsId() {
        val poll = PoolProtocol.poll(
            """{"enabled":true,"request":{"id":"r-3","messages":[{"role":"user","content":[{"type":"image_url"}]}]}}""",
        )
        assertNull(poll.request)
        assertEquals("r-3", poll.refusal!!.first)
    }

    @Test fun aLongPieceIsSplitWithoutBreakingACharacter() {
        val text = "a".repeat(MOST_DELTA_CHARS - 1) + "😀" + "b"
        val pieces = PoolProtocol.pieces(text)
        assertEquals(text, pieces.joinToString(""))
        assertTrue(pieces.all { it.toByteArray().size <= 16 * 1024 && !it.last().isHighSurrogate() })
    }

    @Test fun theGateFollowsBatteryAndHeat() {
        val fine = PhoneCondition(true, 5, false, 0)
        assertTrue(gate(fine).accept)
        assertTrue(gate(fine.copy(charging = false)).stop)
        assertTrue(gate(fine.copy(charging = false, batteryPercent = LEAST_BATTERY_PERCENT)).accept)
        assertTrue(gate(fine.copy(powerSave = true)).stop)
        assertFalse(gate(fine.copy(thermalStatus = THERMAL_MODERATE)).stop)
        assertTrue(gate(fine.copy(thermalStatus = THERMAL_SEVERE)).stop)
    }
}
