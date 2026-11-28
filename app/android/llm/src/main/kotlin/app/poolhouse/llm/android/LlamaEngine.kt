/** GGUF models, run by the llama.cpp libraries `native/build.py` makes. */
package app.poolhouse.llm.android

import android.content.Context
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
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.put

private const val TAG = "PoolhouseLlm"

/** The media marker `mtmd_default_marker()` names, which a message carries where a picture goes. */
private const val PICTURE = "<__media__>"

/** Gemma's published sampling settings (generation_config.json). */
private const val TOP_K = 64
private const val TOP_P = 0.95f
private const val TEMPERATURE = 1.0f

/** llama.cpp's own seed value for "choose one". */
private const val ANY_SEED = -1

/** Where llama.cpp puts the pieces of a reply as it writes them. */
fun interface PieceSink {
    fun piece(bytes: ByteArray)
}

internal object LlamaNative {
    external fun initialise(directory: String): String

    external fun load(model: String, projector: String?, gpu: Boolean, context: Int, threads: Int, quantised: Boolean): Long

    external fun describe(handle: Long): String

    external fun stop(handle: Long, stop: Boolean)

    external fun free(handle: Long)

    external fun generate(
        handle: Long,
        messages: ByteArray,
        pictures: Array<ByteArray>,
        maxTokens: Int,
        temperature: Float,
        topK: Int,
        topP: Float,
        seed: Int,
        keep: Boolean,
        sink: PieceSink,
    ): LongArray
}

class LlamaEngine private constructor(
    /** The devices llama.cpp found, one "type, name, description" line each. */
    val devices: List<String>,
    private val threads: Int,
) : LocalEngine {
    override val format = ModelFormat.GGUF

    override fun accelerators(): List<Accelerator> =
        listOfNotNull(Accelerator.GPU.takeIf { devices.any { it.startsWith("gpu\t") } }, Accelerator.CPU)

    override fun load(files: ModelFiles, settings: LoadSettings): LoadedModel {
        val projector = files.projector?.takeIf { settings.images }
        val handle = LlamaNative.load(
            files.weights.absolutePath,
            projector?.absolutePath,
            settings.accelerator == Accelerator.GPU,
            settings.contextSize,
            threads,
            quantised = true,
        )
        val (device, context, cache) = LlamaNative.describe(handle).split('\t')
        Log.i(TAG, "loaded ${files.weights.name} on $device, context $context, cache $cache")
        return LlamaModel(handle, if (device == "CPU") Accelerator.CPU else Accelerator.GPU, context.toInt(), projector != null, "$device, cache $cache")
    }

    companion object {
        /** The engine, or null in a build that carries no llama.cpp libraries. */
        fun orNull(context: Context, threads: Int = defaultThreads()): LlamaEngine? = try {
            System.loadLibrary("poolhouse_llama")
            LlamaEngine(
                LlamaNative.initialise(context.applicationInfo.nativeLibraryDir).lines().filter { it.isNotBlank() },
                threads,
            )
        } catch (missing: UnsatisfiedLinkError) {
            Log.i(TAG, "no llama.cpp in this build: ${missing.message}")
            null
        }

        /** The thread count llama.cpp's own Android example uses: two to four, leaving two processors free. */
        fun defaultThreads(): Int = (Runtime.getRuntime().availableProcessors() - 2).coerceIn(2, 4)
    }
}

private class LlamaModel(
    private val handle: Long,
    override val accelerator: Accelerator,
    override val contextSize: Int,
    override val acceptsImages: Boolean,
    /** The device and cache type in use. */
    val description: String,
) : LoadedModel {
    override fun generate(
        turns: List<ChatTurn>,
        options: GenerationOptions,
        cancel: CancelToken,
        onText: (String) -> Unit,
    ): GenerationResult {
        require(turns.isNotEmpty()) { "There is nothing to answer." }
        require(acceptsImages || turns.all { it.images.isEmpty() }) { "This model was loaded without picture input." }
        val messages = buildJsonArray {
            turns.forEach { turn ->
                addJsonObject {
                    put("role", turn.role.wire)
                    put("content", PICTURE.repeat(turn.images.size) + turn.text)
                }
            }
        }.toString().toByteArray()
        val pictures = turns.flatMap { it.images }.toTypedArray()
        LlamaNative.stop(handle, false)
        val values = cancel.onCancel { LlamaNative.stop(handle, true) }.use {
            LlamaNative.generate(
                handle,
                messages,
                pictures,
                options.maxTokens ?: 0,
                options.temperature?.toFloat() ?: TEMPERATURE,
                options.topK ?: TOP_K,
                options.topP?.toFloat() ?: TOP_P,
                options.seed ?: ANY_SEED,
                options.keep,
                // Each piece ends on a whole character.
                PieceSink { onText(String(it, Charsets.UTF_8)) },
            )
        }
        // {finish, prompt tokens, output tokens, first token us, prompt us, generation us, context used, reused tokens}
        val prompt = values[1].toInt()
        val output = values[2].toInt()
        val read = prompt - values[7].toInt()
        val finish = when {
            cancel.cancelled -> Finish.CANCELLED
            values[0] == 1L -> Finish.LENGTH
            values[0] == 3L -> Finish.CANCELLED
            else -> Finish.STOP
        }
        return GenerationResult(
            finish,
            GenerationStats(
                promptTokens = prompt,
                outputTokens = output,
                timeToFirstTokenMs = values[3].takeIf { it > 0 }?.let { it / 1000 },
                promptTokensPerSecond = if (values[4] > 0 && read > 0) read * 1e6 / values[4] else null,
                outputTokensPerSecond = if (values[5] > 0 && output > 1) output * 1e6 / values[5] else null,
                contextUsed = values[6].toInt(),
                contextSize = contextSize,
            ),
        )
    }

    override fun close() = LlamaNative.free(handle)
}
