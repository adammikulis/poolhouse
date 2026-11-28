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
import app.poolhouse.llm.engine.LoadedModel
import app.poolhouse.llm.engine.LocalEngine
import app.poolhouse.llm.engine.ModelHost
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.model.CatalogModel
import app.poolhouse.llm.model.ModelFileProblem
import java.io.File

/** What one loaded model was loaded with; a change of any of these, or of its file, reads the model in again. */
data class Held(val model: String, val accelerator: Accelerator, val context: Int, val images: Boolean, val file: String?)

/** The context a model starts with, in tokens, until the person picks another (the owner's choice). */
const val DEFAULT_CONTEXT = 16_384

/** Up to what the model packages support. */
val CONTEXT_CHOICES = listOf(8192, DEFAULT_CONTEXT, 32_000)

class LocalModels private constructor(private val context: Context) {
    val store = phoneStore(context)
    val host = ModelHost<Held>()
    private val engine: LocalEngine = LiteRtEngine()
    private val preferences = context.getSharedPreferences("poolhouse-llm", Context.MODE_PRIVATE)

    fun installed(): List<CatalogModel> = Catalog.models.filter(store::installed)

    /** The models an earlier install left on the phone, which the person can open to this one. */
    fun left(): List<CatalogModel> = Catalog.models.filter(store::left)

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

    /** Removes this install's files of the model; false when a file an earlier install left remains. */
    fun delete(model: CatalogModel): Boolean {
        if (host.loadedKey?.model == model.id) host.release()
        preferences.edit().remove("memory:${model.id}").apply()
        return store.delete(model)
    }

    /** Answers on `model`, loading it first when it is not the one held. Blocks the calling thread. */
    fun generate(
        caller: Caller,
        model: CatalogModel,
        turns: List<ChatTurn>,
        options: GenerationOptions,
        cancel: CancelToken,
        onLoaded: (LoadedModel) -> Unit = {},
        onText: (String) -> Unit,
    ): GenerationResult {
        check(store.installed(model)) { "${model.title} is not downloaded." }
        val key = Held(model.id, accelerator, contextFor(model), model.images, store.version(model))
        return host.use(caller, key, cancel, { load(model, key, cancel) }) { loaded ->
            onLoaded(loaded)
            loaded.generate(turns, options, cancel, onText).also { record(model) }
        }
    }

    /** Reads the model in from the shared folder, only while its file holds the pinned bytes before and after. */
    private fun load(model: CatalogModel, key: Held, cancel: CancelToken): LoadedModel {
        val weights = store.verified(model, cancel)
        val loaded = engine.load(weights, LoadSettings(key.accelerator, key.context, key.images, File(context.cacheDir, "llm")))
        if (store.unchanged(model)) return loaded
        loaded.close()
        throw ModelFileProblem("The file of ${model.title} changed while it was read in.")
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
