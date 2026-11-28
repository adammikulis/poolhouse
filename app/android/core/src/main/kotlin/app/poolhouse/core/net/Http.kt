package app.poolhouse.core.net

import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.link.Address
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.URI
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException

const val API_ROOT = "/phone/v1"
private const val MAX_ANSWER = 8 * 1024 * 1024

/** One request to one address over pinned TLS. A body is sent with its length (the computer refuses chunked bodies) and never twice. */
internal fun open(
    tls: PinnedTls,
    address: Address,
    method: String,
    path: String,
    headers: Map<String, String>,
    body: ByteArray?,
    readTimeoutMs: Int,
    connectTimeoutMs: Int,
): HttpsURLConnection {
    val connection = URI("https://${address.authority}$path").toURL().openConnection() as HttpsURLConnection
    tls.apply(connection)
    connection.requestMethod = method
    connection.connectTimeout = connectTimeoutMs
    connection.readTimeout = readTimeoutMs
    connection.instanceFollowRedirects = false
    connection.useCaches = false
    connection.setRequestProperty("Accept", "application/json")
    for ((name, value) in headers) connection.setRequestProperty(name, value)
    if (body != null) {
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setFixedLengthStreamingMode(body.size)
    }
    return connection
}

/** Whether a failure to connect says the certificate is another one. */
internal fun wrongCertificate(error: Throwable): Boolean =
    error is SSLException && generateSequence<Throwable>(error) { it.cause }.take(8).any { it is PinMismatch }

internal fun InputStream.readBounded(limit: Int = MAX_ANSWER): ByteArray {
    val out = ByteArrayOutputStream()
    val buffer = ByteArray(16 * 1024)
    while (true) {
        val read = read(buffer)
        if (read < 0) return out.toByteArray()
        if (out.size() + read > limit) throw IOException("The computer's answer is too long.")
        out.write(buffer, 0, read)
    }
}

/** The `result` of the envelope every answer has, or the error it names (docs/phone-api.md section 1). */
fun resultOf(status: Int, text: String): JsonElement {
    val root = runCatching { PhoneJson.parseToJsonElement(text) as JsonObject }.getOrElse { throw PhoneError.Unreadable() }
    val ok = (root["ok"] as? JsonPrimitive)?.booleanOrNull ?: throw PhoneError.Unreadable()
    if (ok) return root["result"] ?: JsonNull
    val error = root["error"] as? JsonObject
    val code = (error?.get("code") as? JsonPrimitive)?.contentOrNull ?: "invalid"
    val message = (error?.get("message") as? JsonPrimitive)?.contentOrNull ?: "The computer refused the request."
    if (code == "unlinked") throw PhoneError.Unlinked(message)
    throw PhoneError.Refused(status, code, message)
}
