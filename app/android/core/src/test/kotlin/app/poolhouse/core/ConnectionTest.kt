package app.poolhouse.core

import app.poolhouse.core.link.Address
import app.poolhouse.core.link.LinkOffer
import app.poolhouse.core.link.keyWords
import app.poolhouse.core.link.linkPhone
import app.poolhouse.core.net.Link
import app.poolhouse.core.net.PhoneApi
import app.poolhouse.core.net.PhoneClient
import app.poolhouse.core.net.PhoneError
import app.poolhouse.core.store.Connection
import app.poolhouse.core.store.Delivery
import app.poolhouse.core.store.Item
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.PoolStore
import app.poolhouse.core.store.SeenStore
import app.poolhouse.core.store.items
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ServerSocket

/** The phone's side of the connection against a real TLS server that checks what docs/phone-api.md says it checks. */
class ConnectionTest {
    private val computer = SampleComputer()
    private val key = MemoryKey()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @After
    fun stop() {
        scope.cancel()
        computer.close()
    }

    private fun scanned(pin: String = computer.pin, vararg addresses: Address = arrayOf(Address("127.0.0.1", computer.port))) =
        LinkOffer(computer.code, addresses.toList(), pin, "Sample computer", System.currentTimeMillis() / 1000 + 600)

    private fun linked(): Link = runBlocking { linkPhone(scanned(), "Pixel test", key) }

    private fun api(link: Link = linked(), clock: () -> Long = System::currentTimeMillis) = PhoneApi(PhoneClient(link, key, clock))

    private fun closedPort(): Int = ServerSocket(0).use { it.localPort }

    @Test
    fun aScannedCodeLinksOnceTheComputerHasProvedItKnowsIt() {
        val link = linked()
        assertEquals("ph-0123456789ab", link.phone)
        assertEquals(computer.pin, link.pin)
        assertEquals(keyWords(key.publicKey), link.words)
    }

    @Test
    fun aTypedCodeLearnsThePinFromTheComputerThatProvesTheCode() = runBlocking {
        val offer = LinkOffer.typed("127.0.0.1:${computer.port}", "abcde-fghjk").getOrThrow()
        assertEquals(computer.pin, linkPhone(offer, "Pixel test", key).pin)
    }

    @Test
    fun aComputerThatCannotProveTheCodeIsNotLinked() {
        computer.proofIsHonest = false
        val refused = assertThrows(PhoneError.Unreadable::class.java) { linked() }
        assertTrue(refused.message!!, "Nothing was linked" in refused.message!!)
    }

    @Test
    fun aWrongCodeIsTheComputersRefusal() = runBlocking<Unit> {
        val offer = scanned().copy(code = "KKKKKKKKKK")
        val refused = runCatching { linkPhone(offer, "Pixel test", key) }.exceptionOrNull()
        assertEquals("The code is not right.", (refused as PhoneError.Refused).message)
    }

    @Test
    fun anotherCertificateIsNeverSpokenTo() {
        val other = "0".repeat(64)
        assertThrows(PhoneError.WrongComputer::class.java) { runBlocking { linkPhone(scanned(other), "Pixel test", key) } }
        assertTrue(computer.received.isEmpty())
        val link = linked().copy(pin = other)
        computer.received.clear()
        assertThrows(PhoneError.WrongComputer::class.java) { runBlocking { api(link).sessions() } }
        assertTrue(computer.received.isEmpty())
    }

    @Test
    fun theNextAddressIsTriedWhenOneDoesNotAnswer() = runBlocking {
        val link = linkPhone(scanned(computer.pin, Address("127.0.0.1", closedPort()), Address("127.0.0.1", computer.port)), "Pixel test", key)
        assertEquals(computer.port, link.addresses.first().port)
        computer.answers["GET /phone/v1/sessions"] = { computer.ok("""{"sessions":[]}""") }
        val client = PhoneClient(link.copy(addresses = link.addresses.reversed()), key)
        assertTrue(PhoneApi(client).sessions().sessions.isEmpty())
        assertEquals(computer.port, client.address?.port)
        val nowhere = PhoneApi(PhoneClient(link.copy(addresses = listOf(Address("127.0.0.1", closedPort()))), key))
        assertThrows(PhoneError.Unreachable::class.java) { runBlocking { nowhere.sessions() } }
        Unit
    }

    @Test
    fun aRequestSignedAtTheWrongTimeIsSignedAgainAtTheComputersTime() = runBlocking {
        val link = linked()
        computer.skewMs = 3_600_000
        computer.answers["GET /phone/v1/inbox"] = { computer.ok("""{"questions":[],"notices":[]}""") }
        computer.received.clear()
        api(link.copy(clockOffsetMs = 0)).inbox()
        assertEquals(listOf("GET /phone/v1/inbox", "GET /phone/v1/hello", "GET /phone/v1/inbox"), computer.received.toList())
    }

    @Test
    fun idsAndCursorsAreEncodedAsSigned() = runBlocking {
        computer.answers["GET /phone/v1/sessions/agent-a%20b%2Fc/events"] = { computer.ok("""{"events":[],"next":"t:9:0"}""") }
        assertEquals("t:9:0", api().events("agent-a b/c", "t:8:0&x").next)
        assertTrue(computer.received.any { it == "GET /phone/v1/sessions/agent-a%20b%2Fc/events?after=t%3A8%3A0%26x" })
    }

    @Test
    fun theStoreFollowsTheStreamAndAMessageTravelsFromDraftToDelivered() = runBlocking {
        computer.answers["GET /phone/v1/sessions"] = {
            computer.ok("""{"sessions":[{"id":"claude-1","kind":"claude","title":"canned session","state":"working","mode":"next_step","active_at":5}]}""")
        }
        computer.answers["GET /phone/v1/sessions/claude-1/events"] = {
            computer.ok("""{"events":[{"id":"e1","at":1,"type":"reply","text":"canned reply"}],"next":"c1"}""")
        }
        computer.answers["POST /phone/v1/sessions/claude-1/messages"] = {
            computer.ok("""{"message":"m-1","state":"queued","mode":"next_step","note":"canned note"}""")
        }
        val seen = object : SeenStore {
            var held: Map<String, Double> = emptyMap()
            override fun load() = held
            override fun save(seen: Map<String, Double>) {
                held = seen
            }
        }
        val client = PhoneClient(linked(), key)
        val store = PoolStore(PhoneApi(client), client::hello, scope, seen)
        suspend fun until(check: (PoolState) -> Boolean) = withTimeout(10_000) { store.state.first(check) }
        store.resume()
        val online = until { it.connection == Connection.Online && it.sessions.value != null }
        assertTrue(online.canned)
        assertEquals("canned session", online.sessions.value!!.sessions.single().title)
        // A list the computer cannot give says why and invents nothing.
        assertEquals("No such thing.", until { it.devices.error.isNotEmpty() }.devices.error)
        assertEquals(null, store.state.value.devices.value)

        store.openSession("claude-1")
        until { it.transcripts["claude-1"]?.loaded == true }
        computer.push("session", """{"session":"claude-1","event":{"id":"e2","at":2,"type":"reply","text":"canned live reply"}}""")
        until { state -> state.transcripts.getValue("claude-1").events.size == 2 }

        store.send("claude-1", "canned question")
        val waiting = until { state -> state.transcripts.getValue("claude-1").drafts.any { it.message == "m-1" } }
        assertEquals(Delivery.WAITING, (items(waiting.transcripts.getValue("claude-1")).last() as Item.Outgoing).delivery)
        computer.push("session", """{"session":"claude-1","event":{"id":"e3","at":3,"type":"message","message":"m-1","text":"canned question"}}""")
        computer.push("session", """{"session":"claude-1","event":{"id":"e4","at":4,"type":"delivered","message":"m-1"}}""")
        val delivered = until { state -> state.transcripts.getValue("claude-1").events.size == 4 }
        val outgoing = items(delivered.transcripts.getValue("claude-1")).filterIsInstance<Item.Outgoing>()
        assertEquals(listOf(Delivery.DELIVERED), outgoing.map { it.delivery })

        // The computer ends the stream: the phone connects again; a removed phone stops and says so.
        computer.forget()
        computer.endStream()
        until { it.connection is Connection.Unlinked }
        Unit
    }
}
