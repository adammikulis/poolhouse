/** The models the phone offers to download, each pinned to one revision and the bytes of its files. */
package app.poolhouse.llm.model

import app.poolhouse.llm.engine.ModelFormat

const val HUB = "https://huggingface.co"

/** One file of a model, as the Hub lists it at the pinned revision. */
data class ModelFile(val name: String, val bytes: Long, val sha256: String)

data class Licence(val name: String, val url: String)

/** What a model is used for in a file list: its weights, or the image encoder a GGUF model keeps apart. */
data class CatalogModel(
    /** Printable, stable, and at most 80 characters: the pool asks for the model by this. */
    val id: String,
    val title: String,
    val format: ModelFormat,
    val repository: String,
    val revision: String,
    val weights: ModelFile,
    val projector: ModelFile? = null,
    val licence: Licence,
    /** Whether the downloaded files read pictures. */
    val images: Boolean,
    /** The longest conversation the files support on this runtime, in tokens. */
    val contextLimit: Int,
    /** One sentence about what to expect of it. */
    val about: String,
    /** Whether the files run on the graphics processor only. */
    val gpuOnly: Boolean = false,
) {
    val files: List<ModelFile> get() = listOfNotNull(weights, projector)
    val bytes: Long get() = files.sumOf { it.bytes }
    fun url(file: ModelFile): String = "$HUB/$repository/resolve/$revision/${file.name}"
    val page: String get() = "$HUB/$repository/tree/$revision"
}

private val GEMMA_LICENCE = Licence("Apache 2.0 (Gemma 4)", "https://ai.google.dev/gemma/docs/gemma_4_license")

object Catalog {
    val models: List<CatalogModel> = listOf(
        CatalogModel(
            id = "gemma-4-e2b-litert",
            title = "Gemma 4 E2B",
            format = ModelFormat.LITERTLM,
            repository = "litert-community/gemma-4-E2B-it-litert-lm",
            revision = "2e12bfd869b2962984f97a1f2a26807316d534cc",
            weights = ModelFile("gemma-4-E2B-it.litertlm", 2_588_147_712, "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c"),
            licence = GEMMA_LICENCE,
            images = true,
            contextLimit = 32_000,
            about = "A small model for questions, summaries and pictures.",
        ),
        CatalogModel(
            id = "gemma-4-e2b-long-litert",
            title = "Gemma 4 E2B, long conversations",
            format = ModelFormat.LITERTLM,
            repository = "litert-community/gemma-4-E2B-it-litert-lm",
            revision = "2e12bfd869b2962984f97a1f2a26807316d534cc",
            weights = ModelFile("gemma-4-E2B-it-web.litertlm", 2_008_432_640, "3a08e8d94e23b814ae5414469c370c503813949acb8ceaa17e4ebf8a35af35b5"),
            licence = GEMMA_LICENCE,
            images = false,
            contextLimit = 131_072,
            about = "E2B holding up to 128k tokens, text only; reading a long text takes minutes and warms the phone.",
            gpuOnly = true,
        ),
        CatalogModel(
            id = "gemma-4-e4b-litert",
            title = "Gemma 4 E4B",
            format = ModelFormat.LITERTLM,
            repository = "litert-community/gemma-4-E4B-it-litert-lm",
            revision = "f254b5ba6e36f7d89cc7892e268e9877bc20f0a0",
            weights = ModelFile("gemma-4-E4B-it.litertlm", 3_659_530_240, "0b2a8980ce155fd97673d8e820b4d29d9c7d99b8fa6806f425d969b145bd52e0"),
            licence = GEMMA_LICENCE,
            images = true,
            contextLimit = 32_000,
            about = "A larger model than E2B: better answers, slower, more memory.",
        ),
        CatalogModel(
            id = "gemma-4-e2b-gguf",
            title = "Gemma 4 E2B (pool file)",
            format = ModelFormat.GGUF,
            repository = "unsloth/gemma-4-E2B-it-qat-GGUF",
            revision = "66a399f68ddd113b06dff02fca9523e55465d11d",
            weights = ModelFile("gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf", 2_620_370_976, "e531007218dfab990486a5de7676a6932d6ea8dea233d1f698d7c21cf8a16889"),
            projector = ModelFile("mmproj-F16.gguf", 985_654_080, "13c8966d1635d02e6727f27402880614906fa291850c07feda18dbcddf2291b6"),
            licence = GEMMA_LICENCE,
            images = true,
            contextLimit = 131_072,
            about = "The GGUF file the pool's computers use, run by llama.cpp.",
        ),
        CatalogModel(
            id = "gemma-4-e4b-gguf",
            title = "Gemma 4 E4B (pool file)",
            format = ModelFormat.GGUF,
            repository = "unsloth/gemma-4-E4B-it-qat-GGUF",
            revision = "8c5a9e4fd5482e2be20fe0bf013b4c262a8f4265",
            weights = ModelFile("gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf", 4_215_695_776, "df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3"),
            projector = ModelFile("mmproj-F16.gguf", 990_372_672, "6a255159ee4b01b304f633a57f017dd7d5a69d30fff52abb2614bf0813cef034"),
            licence = GEMMA_LICENCE,
            images = true,
            contextLimit = 131_072,
            about = "The larger GGUF file the pool's computers use, run by llama.cpp.",
        ),
    )

    fun byId(id: String): CatalogModel? = models.firstOrNull { it.id == id }
}
