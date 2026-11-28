/** The bodies of the pool operations of docs/phone-api.md, "Using the phone in the pool". */
package app.poolhouse.llm.pool

import app.poolhouse.llm.engine.ChatTurn
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationOptions
import app.poolhouse.llm.engine.GenerationResult
import app.poolhouse.llm.engine.Role
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject

/** The longest wait the computer accepts on one poll, in seconds. */
const val POLL_SECONDS = 25

/** The largest `max_output` a phone may declare. */
const val MOST_OUTPUT = 8192

/** The longest reason a poll may give for taking no work. */
const val MOST_BLOCKED_CHARS = 120

/** The largest piece one post of an answer carries, in characters: at most 16 KiB of UTF-8. */
const val MOST_DELTA_CHARS = 4000

/** The model the phone offers, as each poll describes it; `context` is the window it really serves. */
data class Offered(val id: String, val name: String, val runtime: String, val context: Int)

data class PoolRequest(val id: String, val turns: List<ChatTurn>, val options: GenerationOptions)

/** The answer to a poll: whether pool use is on at the computer, and a request when one waits. */
data class Poll(val enabled: Boolean, val request: PoolRequest?, val refusal: Pair<String, String>? = null)

object PoolProtocol {
    /** A poll for work; with `blocked` the phone says why it takes none and does not wait. */
    fun pollBody(model: Offered, blocked: String? = null): String = buildJsonObject {
        putJsonObject("model") {
            put("id", model.id)
            put("name", model.name)
            put("runtime", model.runtime)
            put("context", model.context)
            put("max_output", model.context.coerceAtMost(MOST_OUTPUT))
        }
        put("blocked", blocked?.take(MOST_BLOCKED_CHARS) ?: "")
        put("wait_s", if (blocked == null) POLL_SECONDS else 0)
    }.toString()

    /** Reads a poll's result. A request this phone cannot answer is returned as a refusal: its id and the reason. */
    fun poll(result: String): Poll {
        val body = Json.parseToJsonElement(result).jsonObject
        val enabled = body.flag("enabled") ?: false
        val request = body["request"] as? JsonObject ?: return Poll(enabled, null)
        val id = request.text("id")?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{1,64}")) }
            ?: throw IllegalArgumentException("A request came with no usable id.")
        return try {
            Poll(enabled, read(id, request))
        } catch (failure: IllegalArgumentException) {
            Poll(enabled, null, id to (failure.message ?: "The request is not understood."))
        }
    }

    private fun read(id: String, request: JsonObject): PoolRequest {
        val messages = request["messages"] as? JsonArray ?: throw IllegalArgumentException("The request has no messages.")
        require((request["tools"] as? JsonArray).isNullOrEmpty()) { "The phone's model takes no tools." }
        val turns = messages.map { element ->
            val message = element as? JsonObject ?: throw IllegalArgumentException("A message is not understood.")
            val role = Role.entries.firstOrNull { it.wire == message.text("role") }
                ?: throw IllegalArgumentException("The phone reads system, user and assistant messages only.")
            val content = message["content"] as? JsonPrimitive
            require(content != null && content.isString) { "The phone's pool model reads text only." }
            ChatTurn(role, content.content)
        }
        require(turns.isNotEmpty()) { "The request has no messages." }
        return PoolRequest(
            id,
            turns,
            GenerationOptions(
                maxTokens = request["max_tokens"]?.jsonPrimitive?.intOrNull,
                temperature = request["temperature"]?.jsonPrimitive?.doubleOrNull,
                // Nothing of another computer's request stays on the phone after its answer.
                keep = false,
            ),
        )
    }

    /** The name the computer gives itself in its answer to `me`, or null. */
    fun computer(result: String): String? =
        Json.parseToJsonElement(result).jsonObject.text("computer")?.filterNot { it.isISOControl() }?.trim()?.take(48)
            ?.ifEmpty { null }

    fun delta(seq: Int, text: String): String = buildJsonObject {
        put("seq", seq)
        put("delta", text)
        put("done", false)
    }.toString()

    fun done(seq: Int, text: String, result: GenerationResult): String = buildJsonObject {
        put("seq", seq)
        put("delta", text)
        put("done", true)
        put("finish_reason", if (result.finish == Finish.LENGTH) Finish.LENGTH.wire else Finish.STOP.wire)
        putJsonObject("usage") {
            put("prompt_tokens", result.stats.promptTokens ?: 0)
            put("completion_tokens", result.stats.outputTokens ?: 0)
        }
    }.toString()

    fun error(seq: Int, reason: String): String = buildJsonObject {
        put("seq", seq)
        put("error", reason)
    }.toString()

    /** Whether the computer still wants the answer. */
    fun carryOn(result: String): Boolean = Json.parseToJsonElement(result).jsonObject.flag("continue") ?: false

    fun switchBody(enabled: Boolean): String = buildJsonObject { put("enabled", enabled) }.toString()

    fun switched(result: String): Boolean = Json.parseToJsonElement(result).jsonObject.flag("enabled")
        ?: throw IllegalArgumentException("The computer did not say whether pool use is on.")

    /** Splits a piece of text into posts no larger than the computer takes, never inside a character. */
    fun pieces(text: String): List<String> {
        val out = mutableListOf<String>()
        var from = 0
        while (from < text.length) {
            var to = (from + MOST_DELTA_CHARS).coerceAtMost(text.length)
            if (to < text.length && text[to - 1].isHighSurrogate()) to--
            out += text.substring(from, to)
            from = to
        }
        return out
    }

    private fun JsonObject.text(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

    private fun JsonObject.flag(key: String): Boolean? = text(key)?.toBooleanStrictOrNull()
}
