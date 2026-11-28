/** The phone's models in one place: what is downloaded, which one answers, and the single model held in memory. */
package app.poolhouse.llm.android

import android.content.Context
import android.os.Debug
import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.Caller
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.ChatTurn
import app.poolhouse.llm.engine.GenerationOptions
import app.poolhouse.llm.engine.GenerationResult
import app.poolhouse.llm.engine.LoadSettings
import app.poolhouse.llm.engine.LocalEngine
import app.poolhouse.llm.engine.ModelFormat
import app.poolhouse.llm.engine.ModelHost
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.model.CatalogModel
import app.poolhouse.llm.model.ModelStore
import java.io.File

/** What one loaded model was loaded with; a change of any of these reads the model in again. */
data class Held(val model: String, val accelerator: Accelerator, val context: Int, val images: Boolean)

/** The context a model starts with, in tokens, until the person picks another: what both runtimes hold on a phone. */
const val DEFAULT_CONTEXT = 8192

val CONTEXT_CHOICES = listOf(4096, DEFAULT_CONTEXT, 16_384, 32_000, 65_536, 131_072)

class LocalModels private constructor(private val context: Context) {
    val store = ModelStore(File(context.filesDir, "llm-models"))
    val host = ModelHost<Held>()
    private val engines: List<LocalEngine> = listOf(LiteRtEngine())
    private val preferences = context.getSharedPreferences("poolhouse-llm", Context.MODE_PRIVATE)

    /** The models this build can run. */
    val offered: List<CatalogModel> get() = Catalog.models.filter { engine(it.format) != null }

    fun engine(format: ModelFormat): LocalEngine? = engines.firstOrNull { it.format == format }

    fun installed(): List<CatalogModel> = offered.filter(store::installed)

    /** The model that answers: the chosen one while it is downloaded, else the first downloaded. */
    fun chosen(): CatalogModel? = installed().let { all ->
        all.firstOrNull { it.id == preferences.getString("model", null) } ?: all.firstOrNull()
    }

    fun choose(model: CatalogModel) = preferences.edit().putString("model", model.id).apply()

    var accelerator: Accelerator
        get() = if (preferences.getString("accelerator", null) == Accelerator.CPU.name) Accelerator.CPU else Accelerator.GPU
        set(value) = preferences.edit().putString("accelerator", value.name).apply()

    var contextTokens: Int
        get() = preferences.getInt("context", DEFAULT_CONTEXT)
        set(value) = preferences.edit().putInt("context", value).apply()

    /** The context `model` is loaded with: the person's choice, no more than the model's files support. */
    fun contextFor(model: CatalogModel): Int = contextTokens.coerceAtMost(model.contextLimit)

    /** The most memory the app held with this model loaded, as measured on this phone. */
    fun measuredMemory(model: CatalogModel): Long? =
        preferences.getLong("memory:${model.id}", 0).takeIf { it > 0 }

    fun delete(model: CatalogModel) {
        if (host.loadedKey?.model == model.id) host.release()
        store.delete(model)
        preferences.edit().remove("memory:${model.id}").apply()
    }

    /** Answers on `model`, loading it first when it is not the one held. Blocks the calling thread. */
    fun generate(
        caller: Caller,
        model: CatalogModel,
        turns: List<ChatTurn>,
        options: GenerationOptions,
        cancel: CancelToken,
        onLoaded: (Accelerator) -> Unit = {},
        onText: (String) -> Unit,
    ): GenerationResult {
        val files = store.files(model) ?: throw IllegalStateException("${model.title} is not downloaded.")
        val engine = engine(model.format) ?: throw IllegalStateException("This build cannot run ${model.format.label} models.")
        val processor = if (model.gpuOnly) Accelerator.GPU else accelerator
        val key = Held(model.id, processor, contextFor(model), model.images)
        return host.use(caller, key, cancel, {
            engine.load(files, LoadSettings(key.accelerator, key.context, key.images, File(context.cacheDir, "llm")))
        }) { loaded ->
            onLoaded(loaded.accelerator)
            loaded.generate(turns, options, cancel, onText).also { record(model) }
        }
    }

    private fun record(model: CatalogModel) {
        val info = Debug.MemoryInfo()
        Debug.getMemoryInfo(info)
        val held = info.totalPss * 1024L
        if (held > (measuredMemory(model) ?: 0)) preferences.edit().putLong("memory:${model.id}", held).apply()
    }

    companion object {
        @Volatile private var shared: LocalModels? = null

        fun of(context: Context): LocalModels = shared ?: synchronized(this) {
            shared ?: LocalModels(context.applicationContext).also { shared = it }
        }
    }
}
