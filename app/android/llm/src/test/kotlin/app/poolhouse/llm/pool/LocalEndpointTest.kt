/**
 * The loopback endpoint's door: token, origin, switch-off and shapes. No model is under test: what answers is marked
 * CANNED-NOT-A-MODEL and every reply of it begins with the marker.
 */
package app.poolhouse.llm.pool

import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationResult
import app.poolhouse.llm.engine.GenerationStats
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

private const val MARK = "[CANNED RESPONSE, NOT A MODEL]"

private class Canned : Lender {
    @Volatile var held = false
    @Volatile var asked: PoolRequest? = null
    @Volatile var stopped: String? = null
    val started = CountDownLatch(1)

    override fun offered() = Offered("CANNED-NOT-A-MODEL", "CANNED-NOT-A-MODEL", "litert-lm", 8192)

    override fun answer(request: PoolRequest, cancel: CancelToken, onText: (String) -> Unit): GenerationResult {
        asked = request
        onText("$MARK hello")
        started.countDown()
        if (held) {
            val release = CountDownLatch(1)
            cancel.onCancel { stopped = cancel.reason; release.countDown() }
            release.await(20, TimeUnit.SECONDS)
        }
        return GenerationResult(if (cancel.cancelled) Finish.CANCELLED else Finish.STOP, GenerationStats(5, 2, null, null, null, 7, 8192))
    }
}

class LocalEndpointTest {
    private val lender = Canned()
    @Volatile private var condition = PhoneCondition(true, 90, false, 0)
    private val endpoint = LocalEndpoint(lender) { condition }

    @After fun end() = endpoint.close()

    private fun call(path: String, body: String? = null, token: String? = endpoint.token): Pair<Int, String> {
        val connection = URL(endpoint.url + path).openConnection() as HttpURLConnection
        token?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
        if (body != null) {
            connection.doOutput = true
            connection.outputStream.use { it.write(body.toByteArray()) }
        }
        val status = connection.responseCode
        val stream = if (status < 400) connection.inputStream else connection.errorStream
        return status to stream.bufferedReader().readText()
    }

    private val request = """{"messages":[{"role":"user","content":"hi"}],"max_tokens":8}"""

    @Test fun itListensOnTheLoopbackAddressOnly() {
        assertTrue(endpoint.url, endpoint.url.startsWith("http://127.0.0.1:"))
        assertEquals(64, endpoint.token.length)
    }

    @Test fun nothingIsAnsweredWithoutTheToken() {
        for (token in listOf(null, "0".repeat(64), endpoint.token.dropLast(1))) {
            assertEquals(401, call("/chat/completions", request, token).first)
        }
        assertEquals(null, lender.asked)
    }

    @Test fun aWebPageIsRefusedEvenWithTheToken() {
        val socket = java.net.Socket("127.0.0.1", URL(endpoint.url).port)
        socket.use {
            it.getOutputStream().write(
                "GET /v1/models HTTP/1.1\r\nOrigin: https://example.test\r\nAuthorization: Bearer ${endpoint.token}\r\n\r\n".toByteArray(),
            )
            assertTrue(it.getInputStream().bufferedReader().readLine().startsWith("HTTP/1.1 403"))
        }
    }

    @Test fun theModelAndItsReplyHaveTheOpenAiShapes() {
        assertTrue(call("/models").second.contains("\"id\":\"CANNED-NOT-A-MODEL\""))
        val (status, body) = call("/chat/completions", request)
        assertEquals(200, status)
        assertTrue(body, body.contains("\"content\":\"$MARK hello\"") && body.contains("\"finish_reason\":\"stop\""))
        assertFalse(lender.asked!!.options.keep)
    }

    @Test fun aStreamedReplyEndsWithDone() {
        val (status, body) = call("/chat/completions", """{"messages":[{"role":"user","content":"hi"}],"stream":true}""")
        assertEquals(200, status)
        assertTrue(body, body.startsWith("data: ") && body.contains(MARK) && body.trim().endsWith("data: [DONE]"))
    }

    @Test fun toolsAreRefusedAndAHotPhoneSaysSo() {
        assertEquals(400, call("/chat/completions", """{"messages":[{"role":"user","content":"hi"}],"tools":[{}]}""").first)
        condition = condition.copy(thermalStatus = THERMAL_SEVERE)
        val (status, body) = call("/chat/completions", request)
        assertEquals(503, status)
        assertTrue(body, body.contains("too hot"))
    }

    @Test fun closingEndsARunningReplyAndStopsListening() {
        lender.held = true
        val caller = thread { runCatching { call("/chat/completions", """{"messages":[{"role":"user","content":"hi"}],"stream":true}""") } }
        assertTrue(lender.started.await(10, TimeUnit.SECONDS))
        endpoint.close()
        caller.join(10_000)
        assertEquals("Pool use was turned off on the phone.", lender.stopped)
        assertThrows(ConnectException::class.java) { call("/models") }
    }
}
