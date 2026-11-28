/** The models on the phone: where their files are, which are whole and verified, and removing them. */
package app.poolhouse.llm.model

import app.poolhouse.llm.engine.ModelFiles
import java.io.File

private const val VERIFIED = ".verified"
const val PART = ".part"

class ModelStore(private val root: File) {
    fun directory(model: CatalogModel): File = File(root, model.id)

    /** The record that `file` was checked against its pinned checksum after it arrived. */
    private fun mark(model: CatalogModel, file: ModelFile): File = File(directory(model), file.name + VERIFIED)

    fun verified(model: CatalogModel, file: ModelFile): Boolean {
        val held = File(directory(model), file.name)
        val record = mark(model, file)
        return held.isFile && held.length() == file.bytes && record.isFile && record.readText() == file.sha256
    }

    fun markVerified(model: CatalogModel, file: ModelFile) = mark(model, file).writeText(file.sha256)

    fun installed(model: CatalogModel): Boolean = model.files.all { verified(model, it) }

    /** The bytes of this model already on the phone, finished or part way. */
    fun stored(model: CatalogModel): Long = model.files.sumOf { file ->
        if (verified(model, file)) file.bytes else File(directory(model), file.name + PART).length().coerceAtMost(file.bytes)
    }

    /** The files to load, or null while any of them is missing. */
    fun files(model: CatalogModel): ModelFiles? = if (!installed(model)) null else ModelFiles(
        File(directory(model), model.weights.name),
        model.projector?.let { File(directory(model), it.name) },
    )

    /** Removes every file of the model, finished or part way. */
    fun delete(model: CatalogModel): Boolean = directory(model).let { !it.exists() || it.deleteRecursively() }

    fun freeSpace(): Long = root.apply { mkdirs() }.usableSpace
}
