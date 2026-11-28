/**
 * The lent model for a node on the same phone: an OpenAI chat endpoint on the loopback address, behind a token made
 * anew each time it starts. It runs only while the switch is on; what a request holds stays in memory.
 */
package app.poolhouse.llm.pool

import app.poolhouse.llm.engine.Busy
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationResult
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.Semaphore
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/** The sizes a pool request has (docs/phone-api.md, "Using the phone in the pool"). */
private const val MOST_HEAD = 16 * 1024
private const val MOST_BODY = 1 shl 20

/** One request computes and one waits to be refused, as the computer allows two for each phone. */
private const val MOST_CONNECTIONS = 2
private const val READ_TIMEOUT_MS = 15_000

private class Refused(val status: Int, message: String) : RuntimeException(message)

class LocalEndpoint(private val lender: Lender, private val condition: () -> PhoneCondition) : AutoCloseable {
    private val server = ServerSocket(0, MOST_CONNECTIONS, InetAddress.getByName("127.0.0.1"))
    private val places = Semaphore(MOST_CONNECTIONS)
    private val workers = Executors.newCachedThreadPool()
    private val running = ConcurrentHashMap<Socket, CancelToken>()

    val token: String = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
    val url: String = "http://127.0.0.1:${server.localPort}/v1"

    init {
        workers.execute {
            while (!server.isClosed) {
                val socket = runCatching { server.accept() }.getOrNull() ?: continue
                if (!places.tryAcquire()) {
                    socket.close()
                    continue
                }
                running[socket] = CancelToken()
                workers.execute {
                    try {
                        socket.use { serve(it) }
                    } catch (gone: Exception) {
                        // The caller left or sent something unreadable; nothing of it is logged.
                    } finally {
                        running.remove(socket)?.cancel("The request ended.")
                        places.release()
                    }
                }
            }
        }
    }

    /** Stops listening and ends every reply at once. */
    override fun close() {
        server.close()
        running.forEach { (socket, cancel) ->
            cancel.cancel("Pool use was turned off on the phone.")
            runCatching { socket.close() }
        }
        workers.shutdown()
    }

    private fun serve(socket: Socket) {
        socket.soTimeout = READ_TIMEOUT_MS
        val input = socket.getInputStream().buffered()
        val output = socket.getOutputStream()
        try {
            val head = head(input)
            val (method, path) = head.first().split(' ').let { it[0] to it.getOrElse(1) { "" } }
            val headers = head.drop(1).associate { it.substringBefore(':').trim().lowercase() to it.substringAfter(':').trim() }
            // A page in the phone's browser sends its origin; no node does.
            if ("origin" in headers) throw Refused(403, "Web pages may not call this model.")
            val given = headers["authorization"].orEmpty().removePrefix("Bearer ")
            if (!MessageDigest.isEqual(given.toByteArray(), token.toByteArray())) throw Refused(401, "The token is missing or wrong.")
            when {
                method == "GET" && path == "/v1/models" -> answer(output, 200, models().toString())
                method == "POST" && path == "/v1/chat/completions" -> {
                    val length = headers["content-length"]?.toIntOrNull() ?: throw Refused(411, "Send the length of the body.")
                    if (length !in 0..MOST_BODY) throw Refused(413, "The request is too large.")
                    val body = ByteArray(length)
                    var read = 0
                    while (read < length) {
                        val count = input.read(body, read, length - read)
                        if (count < 0) throw Refused(400, "The body ended early.")
                        read += count
                    }
                    chat(String(body, Charsets.UTF_8), output, running.getValue(socket))
                }
                else -> throw Refused(404, "There is no such operation.")
            }
        } catch (refused: Refused) {
            answer(output, refused.status, error(refused.message.orEmpty()))
        }
    }

    private fun models(): JsonObject = buildJsonObject {
        put("object", "list")
        putJsonArray("data") {
            lender.offered()?.let { model ->
                addJsonObject {
                    put("id", model.id)
                    put("object", "model")
                    put("context_length", model.context)
                }
            }
        }
    }

    private fun chat(text: String, output: OutputStream, cancel: CancelToken) {
        val body = runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull() ?: throw Refused(400, "The body is not JSON.")
        val model = lender.offered() ?: throw Refused(503, "The phone has no model downloaded.")
        val now = gate(condition())
        if (!now.accept) throw Refused(503, now.reason.orEmpty())
        // The pool's request shape carries the conversation; this endpoint reads the same one.
        val wrapped = buildJsonObject {
            put("enabled", true)
            putJsonObject("request") {
                put("id", "local")
                body.forEach { (key, value) -> if (key != "id") put(key, value) }
            }
        }
        val poll = PoolProtocol.poll(wrapped.toString())
        val request = poll.request ?: throw Refused(400, poll.refusal?.second ?: "The request is not understood.")
        val stream = body["stream"]?.toString() == "true"
        val reply = StringBuilder()
        var started = false
        fun event(data: String) {
            if (!started) {
                output.write(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".toByteArray(),
                )
                started = true
            }
            output.write("data: $data\n\n".toByteArray())
            output.flush()
        }
        val result = try {
            lender.answer(request, cancel) { piece ->
                if (stream) {
                    // A caller that left stops the reply.
                    runCatching { event(chunk(model.id, piece, null)) }.onFailure { cancel.cancel("The caller left.") }
                } else {
                    reply.append(piece)
                }
            }
        } catch (busy: Busy) {
            throw Refused(503, busy.message.orEmpty())
        } catch (failure: RuntimeException) {
            if (started) return event(error(failure.message ?: "The phone could not answer."))
            throw Refused(500, failure.message ?: "The phone could not answer.")
        }
        if (result.finish == Finish.CANCELLED) {
            if (started) return event(error(cancel.reason ?: "The reply was stopped."))
            throw Refused(503, cancel.reason ?: "The reply was stopped.")
        }
        if (stream) {
            event(chunk(model.id, "", result))
            event("[DONE]")
        } else {
            answer(output, 200, completion(model.id, reply.toString(), result))
        }
        reply.setLength(0)
    }

    private fun chunk(model: String, text: String, done: GenerationResult?): String = buildJsonObject {
        put("object", "chat.completion.chunk")
        put("model", model)
        putJsonArray("choices") {
            addJsonObject {
                put("index", 0)
                putJsonObject("delta") { if (text.isNotEmpty()) put("content", text) }
                put("finish_reason", done?.finish?.wire)
            }
        }
        if (done != null) putJsonObject("usage") { usage(done) }
    }.toString()

    private fun completion(model: String, text: String, result: GenerationResult): String = buildJsonObject {
        put("object", "chat.completion")
        put("model", model)
        putJsonArray("choices") {
            addJsonObject {
                put("index", 0)
                putJsonObject("message") {
                    put("role", "assistant")
                    put("content", text)
                }
                put("finish_reason", result.finish.wire)
            }
        }
        putJsonObject("usage") { usage(result) }
    }.toString()

    private fun kotlinx.serialization.json.JsonObjectBuilder.usage(result: GenerationResult) {
        put("prompt_tokens", result.stats.promptTokens ?: 0)
        put("completion_tokens", result.stats.outputTokens ?: 0)
    }

    private fun error(message: String): String = buildJsonObject { putJsonObject("error") { put("message", message) } }.toString()

    private fun answer(output: OutputStream, status: Int, body: String) {
        val bytes = body.toByteArray()
        output.write(
            ("HTTP/1.1 $status ${if (status == 200) "OK" else "Refused"}\r\nContent-Type: application/json\r\n" +
                "Content-Length: ${bytes.size}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").toByteArray(),
        )
        output.write(bytes)
        output.flush()
    }

    /** The request line and headers. */
    private fun head(input: InputStream): List<String> {
        val bytes = StringBuilder()
        while (!bytes.endsWith("\r\n\r\n")) {
            val next = input.read()
            if (next < 0 || bytes.length >= MOST_HEAD) throw Refused(400, "The request is not understood.")
            bytes.append(next.toChar())
        }
        return bytes.toString().split("\r\n").filter { it.isNotEmpty() }.ifEmpty { throw Refused(400, "The request is empty.") }
    }
}
