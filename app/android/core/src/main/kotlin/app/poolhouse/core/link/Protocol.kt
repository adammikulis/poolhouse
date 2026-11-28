package app.poolhouse.core.link

import java.security.MessageDigest
import java.security.PublicKey
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/*
 * The bytes of docs/phone-api.md sections 2 and 3: linking with the code, and the signature on every later request.
 * tests/fixtures/phone-api-vectors.json holds values made by a second implementation; both sides are checked against it.
 */

const val DEFAULT_PORT = 7450
const val CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
const val CODE_LENGTH = 10
private const val ITERATIONS = 200_000

fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)

fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }

fun base64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)

/** A typed or scanned code as the computer reads it: uppercase, letters and digits only. */
fun normalizeCode(text: String): String = text.uppercase().filter { it in 'A'..'Z' || it in '0'..'9' }

fun validCode(code: String): Boolean = code.length == CODE_LENGTH && code.all { it in CODE_ALPHABET }

/** The base64 X.509 SubjectPublicKeyInfo of the phone's key. */
fun encodeKey(key: PublicKey): String = base64(key.encoded)

/** The 24 hex digits both screens show for a key, in groups of four. */
fun keyWords(key: PublicKey): String = hex(sha256(key.encoded)).take(24).chunked(4).joinToString(" ")

/** K, the key only the computer that showed the code and the phone that read it can make. Slow on purpose: call it off the UI. */
class CodeKey(code: String, private val pin: String) {
    private val key: ByteArray = run {
        val spec = PBEKeySpec(normalizeCode(code).toCharArray(), "poolhouse/phone-link/v1|$pin".toByteArray(Charsets.UTF_8), ITERATIONS, 256)
        SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
    }

    private fun mac(message: String): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return mac.doFinal(message.toByteArray(Charsets.UTF_8))
    }

    fun proof(publicKey: String, name: String): String = base64(mac(linkMessage(pin, publicKey, name)))

    /** Whether the computer's `proof` shows it knows the code: compared in constant time, before anything is kept. */
    fun computerProved(publicKey: String, phone: String, proof: String): Boolean {
        val said = runCatching { Base64.getDecoder().decode(proof) }.getOrNull() ?: return false
        return MessageDigest.isEqual(said, mac("poolhouse/phone-linked/v1\n$pin\n$publicKey\n$phone"))
    }
}

/** M, what the phone proves with the code and signs with its key when it links. */
fun linkMessage(pin: String, publicKey: String, name: String): String = "poolhouse/phone-link/v1\n$pin\n$publicKey\n$name"

/** The string every later request signs. `path` is the path with its query exactly as sent. */
fun requestMessage(pin: String, phone: String, method: String, path: String, timeMs: Long, nonce: String, body: ByteArray): String =
    listOf("poolhouse/phone-request/v1", pin, phone, method, path, timeMs.toString(), nonce, hex(sha256(body))).joinToString("\n")

private val UNPRINTABLE = setOf(
    Character.CONTROL, Character.FORMAT, Character.PRIVATE_USE, Character.SURROGATE, Character.UNASSIGNED,
).map { it.toInt() }

/** A name the computer accepts: 1 to 48 printable characters. */
fun cleanName(name: String): String = name.map { if (it.isWhitespace()) ' ' else it }
    .filter { Character.getType(it) !in UNPRINTABLE }.joinToString("").trim().take(48).trim().ifEmpty { "Android phone" }
