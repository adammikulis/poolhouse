package app.poolhouse.core.net

import app.poolhouse.core.api.Hello
import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.link.Address
import app.poolhouse.core.link.base64
import app.poolhouse.core.link.requestMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runInterruptible
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonElement
import java.io.IOException
import java.security.PublicKey
import java.security.SecureRandom
import javax.net.ssl.HttpsURLConnection

/** The phone's key: Android Keystore on a phone, a key in memory in a test. */
interface PhoneKey {
    val publicKey: PublicKey

    /** SHA256withECDSA over `data`, in DER. */
    fun sign(data: ByteArray): ByteArray
}

/** What the phone keeps of its linked computer. Nothing here is a secret: the key that proves the phone never leaves the Keystore. */
data class Link(
    val phone: String,
    val pin: String,
    val computer: String,
    val words: String,
    val addresses: List<Address>,
    /** The computer's clock minus the phone's, from `/hello`. */
    val clockOffsetMs: Long = 0,
)

/** The server-sent events of `/stream`: a name and its data, or [Opened] once the computer has accepted the request. */
sealed interface StreamItem {
    data object Opened : StreamItem

    data class Message(val event: String, val data: String) : StreamItem
}

private const val CONNECT_MS = 4_000
private const val READ_MS = 20_000
private const val STREAM_SILENCE_MS = 40_000

/**
 * The signed calls to the linked computer (docs/phone-api.md section 3). Each call tries the addresses in order, the one that
 * answered last first; a request is moved to another address only while it has not been sent.
 */
class PhoneClient(
    link: Link,
    private val key: PhoneKey,
    private val clock: () -> Long = System::currentTimeMillis,
    private val random: SecureRandom = SecureRandom(),
) {
    @Volatile
    var link: Link = link
        private set

    private val tls = PinnedTls(link.pin)

    @Volatile
    private var preferred: Address? = null

    /** The address the last answer came from. */
    val address: Address? get() = preferred

    private fun addresses(): List<Address> {
        val first = preferred
        return if (first == null) link.addresses else listOf(first) + link.addresses.filter { it != first }
    }

    private fun headers(method: String, path: String, body: ByteArray): Map<String, String> {
        val time = clock() + link.clockOffsetMs
        val nonce = ByteArray(18).also(random::nextBytes).let { base64(it).replace('+', '-').replace('/', '_') }
        val signature = key.sign(requestMessage(link.pin, link.phone, method, path, time, nonce, body).toByteArray(Charsets.UTF_8))
        return mapOf(
            "Poolhouse-Phone" to link.phone,
            "Poolhouse-Time" to time.toString(),
            "Poolhouse-Nonce" to nonce,
            "Poolhouse-Signature" to base64(signature),
        )
    }

    /** Connects to the first address that accepts the connection. */
    private fun connected(method: String, path: String, signed: Boolean, body: ByteArray?, readMs: Int): HttpsURLConnection {
        var last: IOException? = null
        var wrong = false
        for (address in addresses()) {
            val headers = if (signed) headers(method, path, body ?: ByteArray(0)) else emptyMap()
            val connection = open(tls, address, method, path, headers, body, readMs, CONNECT_MS)
            try {
                connection.connect()
                preferred = address
                return connection
            } catch (error: IOException) {
                connection.disconnect()
                if (wrongCertificate(error)) wrong = true
                last = error
            }
        }
        preferred = null
        if (wrong) throw PhoneError.WrongComputer()
        throw PhoneError.Unreachable(cause = last)
    }

    private fun exchange(method: String, path: String, signed: Boolean, body: ByteArray?, readMs: Int): JsonElement {
        val connection = connected(method, path, signed, body, readMs)
        try {
            if (body != null) connection.outputStream.use { it.write(body) }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            val text = stream?.use { it.readBounded().toString(Charsets.UTF_8) } ?: ""
            return resultOf(status, text)
        } catch (error: IOException) {
            preferred = null
            throw PhoneError.Unreachable("The connection to the computer broke.", error)
        }
    }

    /** `GET /hello`: what the computer says before any signature, and the difference between the two clocks. */
    suspend fun hello(): Hello = runInterruptible(Dispatchers.IO) { readHello() }

    private fun readHello(): Hello {
        val hello = decode(Hello.serializer(), exchange("GET", "$API_ROOT/hello", false, null, READ_MS))
        if (hello.service != "poolhouse-phone") throw PhoneError.Unreadable("That address is not a Poolhouse phone service.")
        if (hello.nowMs > 0) link = link.copy(clockOffsetMs = hello.nowMs - clock())
        return hello
    }

    private fun signed(method: String, path: String, body: ByteArray?, readMs: Int): JsonElement =
        try {
            exchange(method, path, true, body, readMs)
        } catch (error: PhoneError.Refused) {
            // A refused signature changed nothing on the computer: learn its time and send the request once more.
            if (error.code != "signature") throw error
            readHello()
            exchange(method, path, true, body, readMs)
        }

    /** A signed call of `path` (below `/phone/v1`, already percent-encoded) with a JSON body, giving the `result`. */
    suspend fun call(method: String, path: String, body: String? = null, readMs: Int = READ_MS): JsonElement =
        runInterruptible(Dispatchers.IO) { signed(method, "$API_ROOT$path", body?.toByteArray(Charsets.UTF_8), readMs) }

    suspend fun <T> get(path: String, serializer: KSerializer<T>): T = decode(serializer, call("GET", path))

    suspend fun <B, T> post(path: String, body: KSerializer<B>, value: B, result: KSerializer<T>): T =
        decode(result, call("POST", path, PhoneJson.encodeToString(body, value)))

    /**
     * The live stream. The flow completes when the computer closes it and fails when it stays silent for 40 seconds or breaks; the
     * caller connects again and reads what it missed, since the stream replays nothing.
     */
    fun stream(): Flow<StreamItem> = callbackFlow {
        var connection: HttpsURLConnection? = null
        val reader = launch(Dispatchers.IO) {
            try {
                val opened = connected("GET", "$API_ROOT/stream", true, null, STREAM_SILENCE_MS)
                connection = opened
                val status = opened.responseCode
                if (status != 200) {
                    resultOf(status, opened.errorStream?.use { it.readBounded().toString(Charsets.UTF_8) } ?: "")
                    throw PhoneError.Unreadable()
                }
                trySend(StreamItem.Opened)
                val lines = opened.inputStream.bufferedReader(Charsets.UTF_8)
                val parser = EventStreamParser()
                while (true) {
                    val line = lines.readLine() ?: break
                    parser.line(line)?.let { send(it) }
                }
                close()
            } catch (error: CancellationException) {
                throw error
            } catch (error: PhoneError) {
                if (error is PhoneError.Refused && error.code == "signature") runCatching { readHello() }
                close(error)
            } catch (error: IOException) {
                close(PhoneError.Unreachable("The live connection to the computer broke.", error))
            }
        }
        awaitClose {
            // Closing the socket is what ends a blocked read; it is done off the caller's thread, which may be the screen's.
            val open = connection
            if (open != null) Thread { runCatching { open.disconnect() } }.start()
            reader.cancel()
        }
    }.buffer(256)

    private fun <T> decode(serializer: KSerializer<T>, element: JsonElement): T =
        runCatching { PhoneJson.decodeFromJsonElement(serializer, element) }.getOrElse { throw PhoneError.Unreadable(cause = it) }
}

/** Server-sent events, line by line: `event:` and `data:` lines end at a blank line; lines starting with `:` are comments. */
class EventStreamParser {
    private var event = ""
    private val data = StringBuilder()

    fun line(line: String): StreamItem.Message? {
        if (line.isEmpty()) {
            val message = if (data.isEmpty()) null else StreamItem.Message(event.ifEmpty { "message" }, data.toString())
            event = ""
            data.setLength(0)
            return message
        }
        if (line.startsWith(":")) return null
        val field = line.substringBefore(':')
        val value = if (':' in line) line.substringAfter(':').removePrefix(" ") else ""
        when (field) {
            "event" -> event = value
            "data" -> {
                if (data.isNotEmpty()) data.append('\n')
                if (data.length + value.length <= 1 shl 20) data.append(value)
            }
        }
        return null
    }
}
