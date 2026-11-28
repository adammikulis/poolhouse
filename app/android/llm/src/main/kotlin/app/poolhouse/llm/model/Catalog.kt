/** The models the phone offers to download, each one file pinned to a revision and its bytes. */
package app.poolhouse.llm.model

const val HUB = "https://huggingface.co"

/** The file of a model, as the Hub lists it at the pinned revision. */
data class ModelFile(val name: String, val bytes: Long, val sha256: String)

data class Licence(val name: String, val url: String)

data class CatalogModel(
    /** Printable, stable, and at most 80 characters: the pool asks for the model by this. */
    val id: String,
    val title: String,
    val repository: String,
    val revision: String,
    /** The one file that holds the model, its picture encoder included. */
    val file: ModelFile,
    val licence: Licence,
    /** Whether the file reads pictures. */
    val images: Boolean,
    /** The longest conversation the file supports, in tokens. */
    val contextLimit: Int,
    /** One sentence about what to expect of it. */
    val about: String,
) {
    val bytes: Long get() = file.bytes
    val url: String get() = "$HUB/$repository/resolve/$revision/${file.name}"
    val page: String get() = "$HUB/$repository/tree/$revision"
}

private val GEMMA_LICENCE = Licence("Apache 2.0 (Gemma 4)", "https://ai.google.dev/gemma/docs/gemma_4_license")

object Catalog {
    val models: List<CatalogModel> = listOf(
        CatalogModel(
            id = "gemma-4-e2b-litert",
            title = "Gemma 4 E2B",
            repository = "litert-community/gemma-4-E2B-it-litert-lm",
            revision = "2e12bfd869b2962984f97a1f2a26807316d534cc",
            file = ModelFile("gemma-4-E2B-it.litertlm", 2_588_147_712, "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c"),
            licence = GEMMA_LICENCE,
            images = true,
            contextLimit = 32_000,
            about = "A small, fast model for questions, summaries and pictures.",
        ),
        CatalogModel(
            id = "gemma-4-e4b-litert",
            title = "Gemma 4 E4B",
            repository = "litert-community/gemma-4-E4B-it-litert-lm",
            revision = "f254b5ba6e36f7d89cc7892e268e9877bc20f0a0",
            file = ModelFile("gemma-4-E4B-it.litertlm", 3_659_530_240, "0b2a8980ce155fd97673d8e820b4d29d9c7d99b8fa6806f425d969b145bd52e0"),
            licence = GEMMA_LICENCE,
            images = true,
            contextLimit = 32_000,
            about = "A larger model than E2B: slower and needs more memory.",
        ),
    )

    fun byId(id: String): CatalogModel? = models.firstOrNull { it.id == id }
}
