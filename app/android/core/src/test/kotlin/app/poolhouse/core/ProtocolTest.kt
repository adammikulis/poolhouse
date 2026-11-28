package app.poolhouse.core

import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.link.Address
import app.poolhouse.core.link.CodeKey
import app.poolhouse.core.link.LinkOffer
import app.poolhouse.core.link.cleanName
import app.poolhouse.core.link.encodeKey
import app.poolhouse.core.link.keyWords
import app.poolhouse.core.link.linkMessage
import app.poolhouse.core.link.normalizeCode
import app.poolhouse.core.link.requestMessage
import app.poolhouse.core.net.EventStreamParser
import app.poolhouse.core.net.PhoneError
import app.poolhouse.core.net.StreamItem
import app.poolhouse.core.net.resultOf
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

/** The bytes of docs/phone-api.md against tests/fixtures/phone-api-vectors.json, which a second implementation computed. */
class ProtocolTest {
    private val vectors: JsonObject = PhoneJson.parseToJsonElement(
        File(System.getProperty("poolhouse.root"), "tests/fixtures/phone-api-vectors.json").readText(),
    ).jsonObject

    private fun v(name: String) = vectors.getValue(name).jsonPrimitive.content

    private val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(Base64.getDecoder().decode(v("key"))))

    @Test
    fun theCodeMakesTheSameProofsAsTheOtherSide() {
        assertEquals(v("code"), normalizeCode(v("typed_code")))
        assertEquals(v("key"), encodeKey(publicKey))
        assertEquals(v("words"), keyWords(publicKey))
        assertEquals(v("link_message"), linkMessage(v("pin"), v("key"), v("name")))
        val codeKey = CodeKey(v("typed_code"), v("pin"))
        assertEquals(v("link_proof"), codeKey.proof(v("key"), v("name")))
        assertTrue(codeKey.computerProved(v("key"), v("phone"), v("computer_proof")))
        assertFalse(codeKey.computerProved(v("key"), "ph-ffffffffffff", v("computer_proof")))
        assertFalse(codeKey.computerProved(v("key"), v("phone"), "not base64!"))
        assertFalse(CodeKey("ABCDEFGHJM", v("pin")).computerProved(v("key"), v("phone"), v("computer_proof")))
    }

    @Test
    fun everyRequestIsSignedOverTheStringOfSection3() {
        for (entry in vectors.getValue("requests").jsonArray) {
            val r = entry.jsonObject
            fun s(name: String) = r.getValue(name).jsonPrimitive.content
            val message = requestMessage(v("pin"), v("phone"), s("method"), s("path"), s("time_ms").toLong(), s("nonce"), s("body").toByteArray())
            assertArrayEquals(s("message").toByteArray(), message.toByteArray())
            // The signature another implementation made verifies with Java's SHA256withECDSA over these bytes, DER as Android makes it.
            val check = Signature.getInstance("SHA256withECDSA")
            check.initVerify(publicKey)
            check.update(message.toByteArray())
            assertTrue(check.verify(Base64.getDecoder().decode((r.getValue("signature") as JsonPrimitive).content)))
        }
    }

    @Test
    fun aScannedLinkSaysWhereAndWhich() {
        val offer = LinkOffer.parse(v("link")).getOrThrow()
        assertEquals(v("pin"), offer.pin)
        assertEquals(v("code"), offer.code)
        assertEquals(listOf(Address("192.168.1.4", 7450), Address("100.101.2.3", 7450), Address("fd7a:115c:a1e0::1", 7450)), offer.addresses)
        assertEquals("[fd7a:115c:a1e0::1]:7450", offer.addresses[2].authority)
        assertEquals("Sample computer", offer.computer)
        assertFalse(offer.expired(1790000599))
        assertTrue(offer.expired(1790000600))
    }

    @Test
    fun anotherLinkIsRefusedWithASentence() {
        val good = v("link")
        for (bad in listOf("https://example.com", "poolhouse://add-phone?data=x", good.replace("v=1", "v=2"), good.replace("pin=", "pim="),
            good.replace("code=ABCDEFGHJK", "code=ABC"), good.substringBefore("&at="), "%%%")) {
            val refused = LinkOffer.parse(bad)
            assertTrue(bad, refused.isFailure)
            assertTrue(bad, refused.exceptionOrNull()!!.message!!.endsWith("."))
        }
    }

    @Test
    fun aTypedAddressAndCodeAreReadAsAPersonTypesThem() {
        assertEquals(Address("192.168.1.4", 7450), Address.parse(" 192.168.1.4 "))
        assertEquals(Address("studio.local", 9000), Address.parse("https://studio.local:9000/"))
        assertEquals(Address("fd7a:115c::2", 7450), Address.parse("fd7a:115c::2"))
        assertEquals(Address("fd7a:115c::2", 12), Address.parse("[fd7a:115c::2]:12"))
        for (bad in listOf("", "a b", "host:0", "host:70000", "host:x", "user@host", "host/path", "[::1]x")) assertNull(bad, Address.parse(bad))
        val offer = LinkOffer.typed("192.168.1.4", "abcde-fghjk").getOrThrow()
        assertNull(offer.pin)
        assertEquals("ABCDEFGHJK", offer.code)
        assertTrue(LinkOffer.typed("192.168.1.4", "ABCDE-FGHJ0").isFailure)
        assertTrue(LinkOffer.typed("not an address", "ABCDEFGHJK").isFailure)
    }

    @Test
    fun anAnswerGivesItsResultOrTheComputersSentence() {
        assertEquals("""{"a":1}""", resultOf(200, """{"ok":true,"result":{"a":1}}""").toString())
        val refused = assertThrows(PhoneError.Refused::class.java) {
            resultOf(409, """{"ok":false,"error":{"code":"conflict","message":"It already ended."}}""")
        }
        assertEquals("It already ended.", refused.message)
        assertEquals(409, refused.status)
        assertThrows(PhoneError.Unlinked::class.java) { resultOf(401, """{"ok":false,"error":{"code":"unlinked","message":"Removed."}}""") }
        for (bad in listOf("", "<html>", """{"result":1}""", "[]")) assertThrows(bad, PhoneError.Unreadable::class.java) { resultOf(200, bad) }
    }

    @Test
    fun aPhonesNameIsOneTheComputerAccepts() {
        assertEquals("Pixel 9 Pro", cleanName("  Pixel 9\tPro​\u0007 "))
        assertEquals("Android phone", cleanName("‎ \n"))
        assertEquals(48, cleanName("x".repeat(80)).length)
    }

    @Test
    fun theStreamIsReadLineByLine() {
        val parser = EventStreamParser()
        val lines = listOf(": ping", "", "event: session", "data: {\"a\":", "data: 1}", "", "data: x", "", "event: changed", "")
        val out = lines.mapNotNull(parser::line)
        assertEquals(listOf(StreamItem.Message("session", "{\"a\":\n1}"), StreamItem.Message("message", "x")), out)
    }
}
