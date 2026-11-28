/** `.litertlm` models, run by LiteRT-LM. */
@file:OptIn(ExperimentalApi::class)

package app.poolhouse.llm.android

import android.util.Log
import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.ChatTurn
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationOptions
import app.poolhouse.llm.engine.GenerationResult
import app.poolhouse.llm.engine.GenerationStats
import app.poolhouse.llm.engine.LoadSettings
import app.poolhouse.llm.engine.LoadedModel
import app.poolhouse.llm.engine.LocalEngine
import app.poolhouse.llm.engine.ModelFiles
import app.poolhouse.llm.engine.ModelFormat
import app.poolhouse.llm.engine.Role
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.ExperimentalFlags
import com.google.ai.edge.litertlm.LogSeverity
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.SamplerConfig
import java.util.concurrent.CountDownLatch
import java.util.concurrent.atomic.AtomicReference

private const val TAG = "PoolhouseLlm"

/** Gemma's published sampling settings (generation_config.json), used when a request sets only the temperature. */
private const val TOP_K = 64
private const val TOP_P = 0.95

class LiteRtEngine : LocalEngine {
    override val format = ModelFormat.LITERTLM

    override fun accelerators(): List<Accelerator> = listOf(Accelerator.GPU, Accelerator.CPU)

    override fun load(files: ModelFiles, settings: LoadSettings): LoadedModel {
        // The runtime's own log says nothing below a warning, so no message of a conversation reaches the phone's log.
        Engine.setNativeMinLogSeverity(LogSeverity.WARNING)
        ExperimentalFlags.enableBenchmark = true
        settings.cacheDir.mkdirs()
        // The image encoder runs where the weights allow: with the text on the GPU, else on the CPU.
        val tries = buildList {
            if (settings.accelerator == Accelerator.GPU) {
                add(Accelerator.GPU to Accelerator.GPU)
                if (settings.images) add(Accelerator.GPU to Accelerator.CPU)
            }
            add(Accelerator.CPU to Accelerator.CPU)
        }
        var first: Throwable? = null
        for ((text, vision) in tries) {
            val engine = Engine(
                EngineConfig(
                    modelPath = files.weights.absolutePath,
                    backend = backend(text),
                    visionBackend = if (settings.images) backend(vision) else null,
                    audioBackend = null,
                    maxNumTokens = settings.contextSize,
                    maxNumImages = null,
                    cacheDir = settings.cacheDir.absolutePath,
                ),
            )
            try {
                engine.initialize()
                Log.i(TAG, "loaded ${files.weights.name} on ${text.label}, images ${if (settings.images) vision.label else "off"}")
                return LiteRtModel(engine, text, settings.contextSize, settings.images)
            } catch (failure: RuntimeException) {
                Log.w(TAG, "could not load on ${text.label}: ${failure.message}")
                runCatching { engine.close() }
                if (first == null) first = failure
            }
        }
        throw IllegalStateException("LiteRT-LM could not load this model: ${first?.message}", first)
    }

    private fun backend(accelerator: Accelerator): Backend =
        if (accelerator == Accelerator.GPU) Backend.GPU() else Backend.CPU()
}

private class LiteRtModel(
    private val engine: Engine,
    override val accelerator: Accelerator,
    override val contextSize: Int,
    override val acceptsImages: Boolean,
) : LoadedModel {
    private var conversation: Conversation? = null

    /** The messages the open conversation holds, and what it was made with. */
    private var holds: List<ChatTurn> = emptyList()
    private var madeWith: GenerationOptions? = null

    override fun generate(
        turns: List<ChatTurn>,
        options: GenerationOptions,
        cancel: CancelToken,
        onText: (String) -> Unit,
    ): GenerationResult {
        require(turns.isNotEmpty()) { "There is nothing to answer." }
        require(acceptsImages || turns.all { it.images.isEmpty() }) { "This model was loaded without picture input." }
        val open = converse(turns.dropLast(1), options)
        val text = StringBuilder()
        val ended = CountDownLatch(1)
        val failure = AtomicReference<Throwable?>()
        val started = System.nanoTime()
        var firstAt = 0L
        val callback = object : MessageCallback {
            override fun onMessage(message: Message) {
                val piece = message.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }
                if (piece.isEmpty()) return
                if (firstAt == 0L) firstAt = System.nanoTime()
                text.append(piece)
                onText(piece)
            }

            override fun onDone() = ended.countDown()

            override fun onError(throwable: Throwable) {
                failure.set(throwable)
                ended.countDown()
            }
        }
        // A stopped or failed reply leaves the conversation in no known state, so it is dropped unless the reply ends.
        holds = emptyList()
        var kept = false
        try {
            open.sendMessageAsync(message(turns.last()), callback, emptyMap(), null, null, null, options.maxTokens)
            cancel.onCancel { runCatching { open.cancelProcess() } }.use { ended.await() }
            val error = failure.get()
            if (error != null && !cancel.cancelled) throw IllegalStateException("LiteRT-LM stopped: ${error.message}", error)
            val measured = runCatching { open.getBenchmarkInfo() }.getOrNull()
            val used = runCatching { open.getTokenCount() }.getOrNull()
            val output = measured?.lastDecodeTokenCount
            val finish = when {
                cancel.cancelled -> Finish.CANCELLED
                options.maxTokens != null && output != null && output >= options.maxTokens -> Finish.LENGTH
                else -> Finish.STOP
            }
            if (finish != Finish.CANCELLED && options.keep) {
                holds = turns + ChatTurn(Role.ASSISTANT, text.toString())
                kept = true
            }
            val stats = GenerationStats(
                promptTokens = measured?.lastPrefillTokenCount,
                outputTokens = output,
                timeToFirstTokenMs = if (firstAt == 0L) null else (firstAt - started) / 1_000_000,
                promptTokensPerSecond = measured?.lastPrefillTokensPerSecond?.takeIf { it.isFinite() && it > 0 },
                outputTokensPerSecond = measured?.lastDecodeTokensPerSecond?.takeIf { it.isFinite() && it > 0 },
                contextUsed = used,
                contextSize = contextSize,
            )
            return GenerationResult(finish, stats)
        } finally {
            if (!kept) drop()
        }
    }

    private fun converse(history: List<ChatTurn>, options: GenerationOptions): Conversation {
        val held = conversation
        val same = madeWith?.copy(maxTokens = null) == options.copy(maxTokens = null)
        if (held != null && same && holds.isNotEmpty() && holds == history) return held
        drop()
        val system = history.firstOrNull { it.role == Role.SYSTEM }
        val sampler = options.temperature?.let { temperature ->
            if (temperature <= 0) {
                SamplerConfig(1, 1.0, 1.0, options.seed ?: 0)
            } else {
                SamplerConfig(options.topK ?: TOP_K, options.topP ?: TOP_P, temperature, options.seed ?: 0)
            }
        }
        val made = engine.createConversation(
            ConversationConfig(
                systemInstruction = system?.let { Contents.of(it.text) },
                initialMessages = history.filter { it.role != Role.SYSTEM }.map(::message),
                samplerConfig = sampler,
            ),
        )
        conversation = made
        madeWith = options
        return made
    }

    private fun message(turn: ChatTurn): Message = when (turn.role) {
        Role.USER -> Message.user(Contents.of(turn.images.map { Content.ImageBytes(it) } + Content.Text(turn.text)))
        Role.SYSTEM -> Message.system(turn.text)
        Role.ASSISTANT -> Message.model(turn.text)
    }

    private fun drop() {
        holds = emptyList()
        madeWith = null
        conversation?.let { runCatching { it.close() } }
        conversation = null
    }

    override fun close() {
        drop()
        engine.close()
    }
}
