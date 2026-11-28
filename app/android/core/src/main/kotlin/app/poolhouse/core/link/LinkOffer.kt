package app.poolhouse.core.link

import java.net.URI
import java.net.URLDecoder

/** An address the computer listens on. */
data class Address(val host: String, val port: Int) {
    /** The authority part of a URL: an IPv6 address in brackets. */
    val authority: String get() = if (':' in host) "[$host]:$port" else "$host:$port"

    override fun toString(): String = authority

    companion object {
        /** `host`, `host:port`, `[v6]:port` or a bare IPv6 address; the default port when none is written. Null when it is none. */
        fun parse(text: String): Address? {
            val value = text.trim().removePrefix("https://").removeSuffix("/")
            if (value.isEmpty() || value.any { it.isWhitespace() || it == '/' || it == '@' || it == '?' || it == '#' }) return null
            val (host, port) = when {
                value.startsWith("[") -> {
                    val end = value.indexOf(']')
                    if (end < 0) return null
                    val rest = value.substring(end + 1)
                    if (rest.isNotEmpty() && !rest.startsWith(":")) return null
                    value.substring(1, end) to rest.removePrefix(":")
                }
                value.count { it == ':' } > 1 -> value to ""
                ':' in value -> value.substringBefore(':') to value.substringAfter(':')
                else -> value to ""
            }
            if (host.isEmpty()) return null
            val number = if (port.isEmpty()) DEFAULT_PORT else port.toIntOrNull() ?: return null
            return if (number in 1..65535) Address(host, number) else null
        }
    }
}

/** What a scanned or typed link says. A typed one has no pin: it is read from the certificate and kept only after the proof. */
data class LinkOffer(
    val code: String,
    val addresses: List<Address>,
    val pin: String? = null,
    val computer: String = "",
    val expires: Long? = null,
) {
    fun expired(nowSeconds: Long): Boolean = expires != null && nowSeconds >= expires

    companion object {
        private val PIN = Regex("[0-9a-f]{64}")

        /** The offer of a `poolhouse://link?...` address, or a sentence saying why it is not one. */
        fun parse(text: String): Result<LinkOffer> = runCatching {
            val uri = URI(text.trim())
            require(uri.scheme == "poolhouse" && uri.host == "link") { "This is not a Poolhouse link code." }
            val query = (uri.rawQuery ?: "").split('&').filter { '=' in it }.map {
                URLDecoder.decode(it.substringBefore('='), "UTF-8") to URLDecoder.decode(it.substringAfter('='), "UTF-8")
            }
            fun one(name: String): String? = query.firstOrNull { it.first == name }?.second
            require(one("v") == "1") { "This code is from a newer Poolhouse. Update the app." }
            val pin = one("pin") ?: ""
            require(PIN.matches(pin)) { "The code does not name the computer's certificate." }
            val code = normalizeCode(one("code") ?: "")
            require(validCode(code)) { "The code in this link is not complete." }
            val addresses = query.filter { it.first == "at" }.mapNotNull { Address.parse(it.second) }
            require(addresses.isNotEmpty()) { "The code does not say where the computer is." }
            LinkOffer(code, addresses.take(8), pin, (one("name") ?: "").take(64), one("expires")?.toLongOrNull())
        }.recoverCatching { throw IllegalArgumentException((it as? IllegalArgumentException)?.message ?: "This is not a Poolhouse link code.") }

        /** The offer a person typed: an address and the ten characters of the code. */
        fun typed(address: String, code: String): Result<LinkOffer> = runCatching {
            val where = requireNotNull(Address.parse(address)) { "Type the computer's address, for example 192.168.1.4." }
            val normalized = normalizeCode(code)
            require(validCode(normalized)) { "The code is $CODE_LENGTH letters and digits, shown as ABCDE-FGHJK." }
            LinkOffer(normalized, listOf(where))
        }
    }
}
