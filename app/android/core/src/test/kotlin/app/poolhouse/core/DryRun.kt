package app.poolhouse.core

import app.poolhouse.core.link.LinkOffer
import app.poolhouse.core.link.linkPhone
import app.poolhouse.core.net.PhoneApi
import app.poolhouse.core.net.PhoneClient
import app.poolhouse.core.net.StreamItem
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.system.exitProcess

/*
 * The app's own client against a real computer, without a phone:
 * `java -cp <test runtime classpath> app.poolhouse.core.DryRunKt "<poolhouse://link?...>" ["a message"]`.
 *
 * It links with a key held in memory, reads the lists, one history and the stream, sends the message to the first session that
 * takes one when a message is given, and unlinks. It prints what each step answered (ids, states and counts, no conversation text).
 */
fun main(args: Array<String>) {
    var failures = 0
    runBlocking {
        suspend fun <T> step(name: String, block: suspend () -> T): T? = try {
            block().also { println("ok   $name: $it") }
        } catch (error: Exception) {
            failures++
            println("FAIL $name: ${error::class.simpleName}: ${error.message}")
            null
        }

        val offer = LinkOffer.parse(args[0]).getOrThrow()
        val key = MemoryKey()
        var linked: app.poolhouse.core.net.Link? = null
        step("link") {
            linkPhone(offer, "Dry run", key).also { linked = it }.let { "phone ${it.phone}, computer ${it.computer}, words ${it.words}" }
        }
        val link = linked ?: return@runBlocking
        val client = PhoneClient(link, key)
        val api = PhoneApi(client)
        step("hello") { client.hello().let { "api ${it.api}, canned ${it.canned}, clock offset ${client.link.clockOffsetMs} ms" } }
        step("me") { api.me().let { "name ${it.name}, pool_use ${it.poolUse}" } }
        step("devices") { api.devices().devices.map { "${it.kind}:${if (it.online) "online" else "offline"}" } }
        var all = emptyList<app.poolhouse.core.api.Session>()
        step("sessions") { api.sessions().sessions.also { all = it }.map { "${it.id} ${it.kind} ${it.state} ${it.mode}" } }
        all.firstOrNull()?.let { first -> step("history of ${first.id}") { api.events(first.id, "").let { "${it.events.map { e -> e.type }}, next ${it.next}" } } }
        val message = args.getOrNull(1)
        val sendable = all.firstOrNull { it.mode != "none" }
        if (message != null) {
            if (sendable == null) println("skip send: no session takes a message")
            else step("send to ${sendable.id}") { api.send(sendable.id, message).let { "${it.state}, mode ${it.mode}, id ${it.message}" } }
        }
        step("stream for 20 s") {
            val seen = LinkedHashMap<String, Int>()
            withTimeoutOrNull(20_000) {
                api.stream().collect { item ->
                    val name = when (item) {
                        StreamItem.Opened -> "opened"
                        is StreamItem.Message -> item.event
                    }
                    seen[name] = (seen[name] ?: 0) + 1
                }
            }
            check("opened" in seen) { "the stream did not open" }
            seen
        }
        if (message != null && sendable != null) step("history after sending") { api.events(sendable.id, "").events.takeLast(4).map { it.type } }
        step("inbox") { api.inbox().let { "${it.questions.size} questions, ${it.notices.size} notices" } }
        step("delegates") { api.delegates().runs.size }
        step("jobs") { api.jobs().jobs.size }
        step("scheduled") { api.scheduled().let { "available ${it.available}, ${it.jobs.size}" } }
        step("models") { api.models().let { "can serve ${it.canServe}, ${it.installed.size} installed, ${it.running.size} running" } }
        step("projects") { api.projects().projects.size }
        step("pool-use") { api.poolUse().let { "enabled ${it.enabled}" } }
        step("unlink") { api.unlink() }
        step("refused after unlink") { runCatching { api.sessions() }.exceptionOrNull()?.let { it::class.simpleName } ?: error("still answered") }
    }
    println("dry run: $failures failure(s)")
    exitProcess(if (failures == 0) 0 else 1)
}
