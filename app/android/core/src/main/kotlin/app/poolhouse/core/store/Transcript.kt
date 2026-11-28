package app.poolhouse.core.store

import app.poolhouse.core.api.Event
import app.poolhouse.core.api.Question

/** A message typed on this phone that the history does not show yet. */
data class Draft(
    val localId: String,
    val text: String,
    val at: Double,
    /** The computer's id for it, once it has accepted it. */
    val message: String = "",
    val mode: String = "",
    val note: String = "",
    /** Why it was not sent, or empty while it is being sent or waiting. */
    val failed: String = "",
)

/** A session's history as the phone holds it: events oldest first, each once. */
data class Transcript(
    val events: List<Event> = emptyList(),
    /** Where the next read starts; empty before the first. */
    val next: String = "",
    val loaded: Boolean = false,
    val error: String = "",
    val drafts: List<Draft> = emptyList(),
    private val ids: Set<String> = events.mapTo(HashSet()) { it.id },
) {
    /** Adds the events not already held. A message of this phone that the history now shows replaces its draft. */
    fun add(more: List<Event>): Transcript {
        val fresh = more.filter { it.id !in ids }.distinctBy { it.id }
        if (fresh.isEmpty()) return this
        val all = events + fresh
        // A page read after a reconnect can hold events older than ones the stream already gave; equal times keep their order.
        val ordered = if (fresh.first().at < (events.lastOrNull()?.at ?: 0.0)) all.sortedBy { it.at } else all
        val seen = fresh.mapNotNull { it.message.ifEmpty { null } }.toSet()
        return Transcript(ordered, next, loaded, error, drafts.filterNot { it.message in seen }, ids + fresh.map { it.id })
    }

    fun withDraft(draft: Draft): Transcript {
        val others = drafts.filterNot { it.localId == draft.localId }
        // The stream can show the message before the call that sent it has returned.
        if (draft.message.isNotEmpty() && events.any { it.message == draft.message }) return copy(drafts = others)
        return copy(drafts = others + draft)
    }

    fun withoutDraft(localId: String): Transcript = copy(drafts = drafts.filterNot { it.localId == localId })

    private fun copy(
        next: String = this.next,
        loaded: Boolean = this.loaded,
        error: String = this.error,
        drafts: List<Draft> = this.drafts,
    ) = Transcript(events, next, loaded, error, drafts, ids)

    fun page(more: List<Event>, cursor: String): Transcript = add(more).copy(next = cursor.ifEmpty { next }, loaded = true, error = "")

    fun failed(why: String): Transcript = copy(error = why)
}

enum class Delivery { SENDING, WAITING, DELIVERED, DROPPED, FAILED }

data class Step(val text: String, val ok: Boolean)

/** What a conversation shows, in order. [key] is stable for the list. */
sealed interface Item {
    val key: String
    val at: Double

    /** What the agent was told, by the computer, by the lead or by this phone. */
    data class Prompt(override val key: String, override val at: Double, val text: String, val from: String) : Item

    /** A message of the phone with how far it has got. */
    data class Outgoing(
        override val key: String,
        override val at: Double,
        val text: String,
        val delivery: Delivery,
        val detail: String = "",
        /** The draft's id when the phone still holds it, so a failed one can be sent again. */
        val draft: String = "",
    ) : Item

    data class Reply(override val key: String, override val at: Double, val text: String) : Item

    /** The tools the agent ran, one line each, grouped while nothing else came between them. */
    data class Steps(override val key: String, override val at: Double, val steps: List<Step>) : Item

    data class Ask(
        override val key: String,
        override val at: Double,
        val question: String,
        val questions: List<Question>,
        val expiresAt: Double?,
        /** `phone` or `computer` once answered, or null while it waits. */
        val answeredBy: String?,
    ) : Item

    data class Notice(override val key: String, override val at: Double, val text: String, val kind: String) : Item
}

fun items(transcript: Transcript): List<Item> {
    val out = ArrayList<Item>()
    val outgoing = HashMap<String, Int>()
    val asks = HashMap<String, Int>()
    val delivered = HashMap<String, Int>()
    for (event in transcript.events) {
        when (event.type) {
            "message" -> {
                outgoing[event.message] = out.size
                out.add(Item.Outgoing(event.id, event.at, event.text, Delivery.WAITING))
            }
            "delivered" -> outgoing[event.message]?.let { index ->
                val sent = out[index] as Item.Outgoing
                out[index] = sent.copy(delivery = Delivery.DELIVERED)
                delivered[sent.text] = (delivered[sent.text] ?: 0) + 1
            }
            "dropped" -> {
                val index = outgoing[event.message]
                if (index != null) out[index] = (out[index] as Item.Outgoing).copy(delivery = Delivery.DROPPED, detail = event.text)
                else out.add(Item.Notice(event.id, event.at, event.text.ifEmpty { "A message was not delivered." }, "dropped"))
            }
            "prompt" -> {
                // A delivered message of this phone comes back as the prompt the agent received: it is shown once.
                val repeats = if (event.from == "phone") delivered[event.text] ?: 0 else 0
                if (repeats > 0) delivered[event.text] = repeats - 1 else out.add(Item.Prompt(event.id, event.at, event.text, event.from))
            }
            "reply" -> out.add(Item.Reply(event.id, event.at, event.text))
            "progress" -> {
                val last = out.lastOrNull()
                if (last is Item.Steps) out[out.lastIndex] = last.copy(steps = last.steps + Step(event.text, event.ok))
                else out.add(Item.Steps(event.id, event.at, listOf(Step(event.text, event.ok))))
            }
            "question" -> {
                asks[event.question] = out.size
                out.add(Item.Ask(event.id, event.at, event.question, event.questions, event.expiresAt, null))
            }
            "answered" -> asks[event.question]?.let { index -> out[index] = (out[index] as Item.Ask).copy(answeredBy = event.by.ifEmpty { "computer" }) }
            "notice" -> out.add(Item.Notice(event.id, event.at, event.text, event.kind))
        }
    }
    for (draft in transcript.drafts) {
        val delivery = when {
            draft.failed.isNotEmpty() -> Delivery.FAILED
            draft.message.isEmpty() -> Delivery.SENDING
            else -> Delivery.WAITING
        }
        out.add(Item.Outgoing(draft.localId, draft.at, draft.text, delivery, draft.failed.ifEmpty { draft.note }, draft.localId))
    }
    return out
}

/** The questions still waiting for an answer, oldest first: what the answer dock above the send box shows. */
fun openQuestions(items: List<Item>, nowSeconds: Double): List<Item.Ask> =
    items.filterIsInstance<Item.Ask>().filter { it.answeredBy == null && (it.expiresAt == null || it.expiresAt > nowSeconds) }
