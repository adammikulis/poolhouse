/** What a model runtime on the phone offers, whatever its file format. */
package app.poolhouse.llm.engine

import java.io.File

enum class ModelFormat(val label: String) { LITERTLM("LiteRT-LM"), GGUF("llama.cpp") }

enum class Accelerator(val label: String) { GPU("GPU"), CPU("CPU") }

enum class Role(val wire: String) { SYSTEM("system"), USER("user"), ASSISTANT("assistant"), TOOL("tool") }

/** A call the model asks for; `arguments` is a JSON object as text. */
data class ToolCall(val id: String, val name: String, val arguments: String)

/**
 * One message of a conversation. `images` are the encoded bytes (JPEG or PNG) that came with it, `toolCalls` the calls an
 * assistant message made, and `toolName` the tool whose answer a tool message carries.
 */
class ChatTurn(
    val role: Role,
    val text: String,
    val images: List<ByteArray> = emptyList(),
    val toolCalls: List<ToolCall> = emptyList(),
    val toolName: String? = null,
) {
    override fun equals(other: Any?): Boolean = other is ChatTurn && role == other.role && text == other.text &&
        toolCalls == other.toolCalls && toolName == other.toolName &&
        images.size == other.images.size && images.indices.all { images[it].contentEquals(other.images[it]) }

    override fun hashCode(): Int = 31 * (31 * role.hashCode() + text.hashCode()) + images.size
}

/** How a reply is drawn. A null value leaves the model's own setting. */
data class GenerationOptions(
    val maxTokens: Int? = null,
    val temperature: Double? = null,
    val topK: Int? = null,
    val topP: Double? = null,
    val seed: Int? = null,
    /** The tools offered, each an OpenAI function object as JSON text. */
    val tools: List<String> = emptyList(),
    /** Whether the runtime may keep the conversation in memory after the reply, to go on from it. */
    val keep: Boolean = true,
)

enum class Finish(val wire: String) { STOP("stop"), LENGTH("length"), TOOL_CALLS("tool_calls"), CANCELLED("cancelled") }

/** What one reply cost, as the runtime counted it. A null count is one the runtime does not report. */
data class GenerationStats(
    val promptTokens: Int?,
    val outputTokens: Int?,
    val timeToFirstTokenMs: Long?,
    val promptTokensPerSecond: Double?,
    val outputTokensPerSecond: Double?,
    val contextUsed: Int?,
    val contextSize: Int,
)

data class GenerationResult(val finish: Finish, val stats: GenerationStats, val toolCalls: List<ToolCall> = emptyList())

/** A request to stop, which a running reply hears at once. */
class CancelToken {
    private val listeners = mutableListOf<() -> Unit>()

    /** Why the reply was stopped, or null while it may run. */
    @Volatile
    var reason: String? = null
        private set

    val cancelled: Boolean get() = reason != null

    fun cancel(why: String) {
        val heard: List<() -> Unit>
        synchronized(this) {
            if (reason != null) return
            reason = why
            heard = listeners.toList()
            listeners.clear()
        }
        heard.forEach { it() }
    }

    /** Runs `listener` once on cancellation, at once when that has happened; the returned handle forgets it. */
    fun onCancel(listener: () -> Unit): AutoCloseable {
        val late = synchronized(this) {
            if (reason == null) listeners += listener
            reason != null
        }
        if (late) listener()
        return AutoCloseable { synchronized(this) { listeners.remove(listener) } }
    }
}

/** The files of one downloaded model. */
data class ModelFiles(val weights: File, val projector: File? = null)

data class LoadSettings(
    val accelerator: Accelerator,
    val contextSize: Int,
    /** Whether to load the image encoder. */
    val images: Boolean,
    /** A directory the runtime may fill with compiled kernels. */
    val cacheDir: File,
)

/** A model held in memory. One reply runs at a time. */
interface LoadedModel : AutoCloseable {
    val accelerator: Accelerator
    val contextSize: Int
    val acceptsImages: Boolean

    /**
     * Writes the reply to the last of `turns` into `onText` piece by piece and returns when it has ended. The calling
     * thread is held for the whole reply; `cancel` ends it early with [Finish.CANCELLED].
     */
    fun generate(turns: List<ChatTurn>, options: GenerationOptions, cancel: CancelToken, onText: (String) -> Unit): GenerationResult
}

interface LocalEngine {
    val format: ModelFormat

    /** The accelerators this build and this phone can offer, best first. */
    fun accelerators(): List<Accelerator>

    fun load(files: ModelFiles, settings: LoadSettings): LoadedModel
}
