/** The models on the phone: where their files are, which are whole and verified, and removing them. */
package app.poolhouse.llm.model

import java.io.File

private const val VERIFIED = ".verified"
const val PART = ".part"

class ModelStore(private val root: File) {
    fun directory(model: CatalogModel): File = File(root, model.id)

    /** The record that the file was checked against its pinned checksum after it arrived. */
    private fun mark(model: CatalogModel): File = File(directory(model), model.file.name + VERIFIED)

    fun installed(model: CatalogModel): Boolean {
        val held = File(directory(model), model.file.name)
        val record = mark(model)
        return held.isFile && held.length() == model.bytes && record.isFile && record.readText() == model.file.sha256
    }

    fun markVerified(model: CatalogModel) = mark(model).writeText(model.file.sha256)

    /** The bytes of this model already on the phone, finished or part way. */
    fun stored(model: CatalogModel): Long =
        if (installed(model)) model.bytes else File(directory(model), model.file.name + PART).length().coerceAtMost(model.bytes)

    /** The file to load, or null while it is not downloaded and verified. */
    fun file(model: CatalogModel): File? = if (installed(model)) File(directory(model), model.file.name) else null

    /** Removes the model's files, finished or part way. */
    fun delete(model: CatalogModel): Boolean = directory(model).let { !it.exists() || it.deleteRecursively() }

    fun freeSpace(): Long = root.apply { mkdirs() }.usableSpace
}
