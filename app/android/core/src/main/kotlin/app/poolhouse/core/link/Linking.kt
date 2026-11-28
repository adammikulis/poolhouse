package app.poolhouse.core.link

import app.poolhouse.core.api.Hello
import app.poolhouse.core.api.LinkRequest
import app.poolhouse.core.api.LinkResult
import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.net.API_ROOT
import app.poolhouse.core.net.Link
import app.poolhouse.core.net.PhoneError
import app.poolhouse.core.net.PhoneKey
import app.poolhouse.core.net.PinnedTls
import app.poolhouse.core.net.open
import app.poolhouse.core.net.readBounded
import app.poolhouse.core.net.resultOf
import app.poolhouse.core.net.wrongCertificate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runInterruptible
import kotlinx.serialization.KSerializer
import java.io.IOException

private const val CONNECT_MS = 4_000
private const val READ_MS = 20_000

/**
 * Links this phone to the computer of `offer` (docs/phone-api.md section 2). The link is returned only after the computer has proved
 * it knows the code, so nothing between the two can have taken its place; until then nothing is to be kept.
 */
suspend fun linkPhone(offer: LinkOffer, name: String, key: PhoneKey, clock: () -> Long = System::currentTimeMillis): Link =
    runInterruptible(Dispatchers.IO) {
        if (offer.expired(clock() / 1000)) throw PhoneError.Refused(403, "refused", "That code has expired. Show a new one on the computer.")
        val found = find(offer, clock)
        val tls = PinnedTls(found.pin)
        val publicKey = encodeKey(key.publicKey)
        val phoneName = cleanName(name)
        val codeKey = CodeKey(offer.code, found.pin)
        val message = linkMessage(found.pin, publicKey, phoneName).toByteArray(Charsets.UTF_8)
        val request = LinkRequest(phoneName, publicKey, codeKey.proof(publicKey, phoneName), base64(key.sign(message)))
        val body = PhoneJson.encodeToString(LinkRequest.serializer(), request).toByteArray(Charsets.UTF_8)
        val result = send(tls, found.address, "POST", "$API_ROOT/link", body, LinkResult.serializer())
        if (!codeKey.computerProved(publicKey, result.phone, result.proof)) {
            throw PhoneError.Unreadable("The computer at that address could not show it knows the code. Nothing was linked.")
        }
        val addresses = listOf(found.address) + offer.addresses.filter { it != found.address }
        Link(
            phone = result.phone,
            pin = found.pin,
            computer = result.computer.ifEmpty { found.hello.computer },
            words = keyWords(key.publicKey),
            addresses = addresses,
            clockOffsetMs = found.hello.nowMs - found.at,
        )
    }

private class Found(val address: Address, val pin: String, val hello: Hello, val at: Long)

/** The first address that answers as a phone service: with the offer's pin, or else the pin of the certificate it showed. */
private fun find(offer: LinkOffer, clock: () -> Long): Found {
    var last: IOException? = null
    var wrong = false
    for (address in offer.addresses) {
        val tls = PinnedTls(offer.pin)
        try {
            val hello = send(tls, address, "GET", "$API_ROOT/hello", null, Hello.serializer())
            if (hello.service != "poolhouse-phone") throw PhoneError.Unreadable("That address is not a Poolhouse phone service.")
            val pin = offer.pin ?: tls.seen ?: throw PhoneError.Unreadable()
            return Found(address, pin, hello, clock())
        } catch (error: PhoneError.Unreachable) {
            wrong = wrong || wrongCertificate(error.cause ?: error)
            last = error.cause as? IOException
        }
    }
    if (wrong) throw PhoneError.WrongComputer()
    throw PhoneError.Unreachable("The computer cannot be reached. Check that the phone is on the same network as the computer.", last)
}

private fun <T> send(tls: PinnedTls, address: Address, method: String, path: String, body: ByteArray?, result: KSerializer<T>): T {
    val connection = open(tls, address, method, path, emptyMap(), body, READ_MS, CONNECT_MS)
    try {
        connection.connect()
        if (body != null) connection.outputStream.use { it.write(body) }
        val status = connection.responseCode
        val stream = if (status >= 400) connection.errorStream else connection.inputStream
        val element = resultOf(status, stream?.use { it.readBounded().toString(Charsets.UTF_8) } ?: "")
        return runCatching { PhoneJson.decodeFromJsonElement(result, element) }.getOrElse { throw PhoneError.Unreadable(cause = it) }
    } catch (error: IOException) {
        throw PhoneError.Unreachable(cause = error)
    } finally {
        connection.disconnect()
    }
}
