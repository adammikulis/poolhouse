package app.poolhouse.core

import app.poolhouse.core.link.base64
import app.poolhouse.core.link.hex
import app.poolhouse.core.link.linkMessage
import app.poolhouse.core.link.requestMessage
import app.poolhouse.core.link.sha256
import app.poolhouse.core.net.PhoneKey
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpsConfigurator
import com.sun.net.httpserver.HttpsServer
import java.io.File
import java.net.InetAddress
import java.net.InetSocketAddress
import java.nio.file.Files
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PublicKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext

/** A phone key held in memory, signing as Android Keystore does. */
class MemoryKey : PhoneKey {
    private val pair: KeyPair = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
    override val publicKey: PublicKey get() = pair.public

    override fun sign(data: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSA").run {
        initSign(pair.private)
        update(data)
        sign()
    }
}

/** A self-signed P-256 certificate made by the JDK's own keytool, as the computer makes one. */
private fun certificate(): Pair<KeyStore, String> {
    val folder = Files.createTempDirectory("sample-computer").toFile()
    val file = File(folder, "computer.p12")
    val keytool = File(System.getProperty("java.home"), "bin/keytool").path
    val made = ProcessBuilder(keytool, "-genkeypair", "-alias", "c", "-keyalg", "EC", "-groupname", "secp256r1", "-dname", "CN=sample",
        "-validity", "2", "-storetype", "PKCS12", "-keystore", file.path, "-storepass", "sample-store", "-keypass", "sample-store")
        .redirectErrorStream(true).start()
    val said = made.inputStream.bufferedReader().readText()
    check(made.waitFor() == 0) { said }
    val store = KeyStore.getInstance("PKCS12").apply { file.inputStream().use { load(it, "sample-store".toCharArray()) } }
    val pin = hex(sha256(store.getCertificate("c").encoded))
    folder.deleteRecursively()
    return store to pin
}

/**
 * The computer's side of docs/phone-api.md as far as these checks need it, on a real TLS 1.3 socket: it checks the code, every
 * signature, its time and its nonce as the page says, and answers the rest with canned data that says it is canned.
 */
class SampleComputer(val code: String = "ABCDEFGHJK", var proofIsHonest: Boolean = true) : AutoCloseable {
    private val identity = certificate()
    val pin: String = identity.second
    private val server = HttpsServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0)
    val port: Int get() = server.address.port
    private val phones = ConcurrentHashMap<String, PublicKey>()
    private val nonces = ConcurrentHashMap<String, Boolean>()

    /** How far the computer's clock is from this process's, in milliseconds. */
    @Volatile
    var skewMs: Long = 0

    /** What was received, as `METHOD path body`. */
    val received = CopyOnWriteArrayList<String>()

    /** The answers of signed requests, by `METHOD path-without-query`. */
    val answers = ConcurrentHashMap<String, () -> Pair<Int, String>>()

    /** Lines to send on the open stream. */
    private val stream = LinkedBlockingQueue<String>()

    init {
        val keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(identity.first, "sample-store".toCharArray()) }
        val tls = SSLContext.getInstance("TLSv1.3").apply { init(keys.keyManagers, null, null) }
        server.httpsConfigurator = HttpsConfigurator(tls)
        server.executor = Executors.newCachedThreadPool { r -> Thread(r).apply { isDaemon = true } }
        server.createContext("/phone/v1/") { handle(it) }
        server.start()
    }

    fun push(event: String, data: String) {
        stream.put("event: $event\ndata: $data\n\n")
    }

    fun endStream() = stream.put("")

    fun forget() = phones.clear()

    fun ok(result: String): Pair<Int, String> = 200 to """{"ok":true,"result":$result}"""

    private fun refuse(status: Int, code: String, message: String) = status to """{"ok":false,"error":{"code":"$code","message":"$message"}}"""

    private fun now() = System.currentTimeMillis() + skewMs

    private fun handle(exchange: HttpExchange) {
        val body = exchange.requestBody.readBytes()
        val path = exchange.requestURI.rawPath + (exchange.requestURI.rawQuery?.let { "?$it" } ?: "")
        val method = exchange.requestMethod
        received.add("$method $path ${body.toString(Charsets.UTF_8)}".trim())
        val answer = when {
            path == "/phone/v1/hello" ->
                ok("""{"service":"poolhouse-phone","api":1,"computer":"Sample computer","now_ms":${now()},"linking":true,"canned":true}""")
            path == "/phone/v1/link" -> link(body.toString(Charsets.UTF_8))
            else -> signed(exchange, method, path, body) ?: if (path == "/phone/v1/stream") return follow(exchange) else {
                answers["$method ${exchange.requestURI.rawPath}"]?.invoke() ?: refuse(404, "missing", "No such thing.")
            }
        }
        val bytes = answer.second.toByteArray()
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.sendResponseHeaders(answer.first, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }

    private fun field(body: String, name: String) = Regex("\"$name\":\"([^\"]*)\"").find(body)!!.groupValues[1].replace("\\/", "/")

    private fun link(body: String): Pair<Int, String> {
        val name = field(body, "name")
        val key = field(body, "key")
        val spec = PBEKeySpec(code.toCharArray(), "poolhouse/phone-link/v1|$pin".toByteArray(), 200_000, 256)
        val secret = SecretKeySpec(SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded, "HmacSHA256")
        fun mac(text: String) = Mac.getInstance("HmacSHA256").run {
            init(secret)
            doFinal(text.toByteArray())
        }
        val public = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(key)))
        val message = linkMessage(pin, key, name)
        val signed = Signature.getInstance("SHA256withECDSA").run {
            initVerify(public)
            update(message.toByteArray())
            verify(Base64.getDecoder().decode(field(body, "signature")))
        }
        if (!signed || field(body, "proof") != base64(mac(message))) return refuse(403, "refused", "The code is not right.")
        val phone = "ph-0123456789ab"
        phones[phone] = public
        val proof = mac("poolhouse/phone-linked/v1\n$pin\n$key\n${if (proofIsHonest) phone else "ph-000000000000"}")
        return ok("""{"phone":"$phone","computer":"Sample computer","words":"canned","proof":"${base64(proof)}"}""")
    }

    /** The refusal of a request, or null when its signature, time and nonce are good. */
    private fun signed(exchange: HttpExchange, method: String, path: String, body: ByteArray): Pair<Int, String>? {
        val headers = exchange.requestHeaders
        val phone = headers.getFirst("Poolhouse-Phone")
        val key = phones[phone] ?: return refuse(401, "unlinked", "This phone is not linked.")
        val time = headers.getFirst("Poolhouse-Time").toLong()
        val nonce = headers.getFirst("Poolhouse-Nonce")
        if (kotlin.math.abs(time - now()) > 120_000 || !Regex("[A-Za-z0-9_-]{16,48}").matches(nonce) || nonces.putIfAbsent(nonce, true) != null) {
            return refuse(401, "signature", "The time or nonce was refused.")
        }
        val good = Signature.getInstance("SHA256withECDSA").run {
            initVerify(key)
            update(requestMessage(pin, phone, method, path, time, nonce, body).toByteArray())
            verify(Base64.getDecoder().decode(headers.getFirst("Poolhouse-Signature")))
        }
        return if (good) null else refuse(401, "signature", "The signature was refused.")
    }

    private fun follow(exchange: HttpExchange) {
        stream.clear()
        exchange.responseHeaders.add("Content-Type", "text/event-stream")
        exchange.sendResponseHeaders(200, 0)
        try {
            exchange.responseBody.use { out ->
                out.write(": ping\n\n".toByteArray())
                out.flush()
                while (true) {
                    val next = stream.poll(200, TimeUnit.MILLISECONDS)
                    if (next == "") break
                    out.write((next ?: ": ping\n\n").toByteArray())
                    out.flush()
                }
            }
        } catch (gone: java.io.IOException) {
            // The phone closed its side.
        }
    }

    override fun close() {
        server.stop(0)
    }
}
