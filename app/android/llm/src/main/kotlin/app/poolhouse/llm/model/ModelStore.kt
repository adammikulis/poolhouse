/**
 * The models on the phone. Their files lie in a folder that outlives the app, so they are checked against their pinned
 * bytes before a load, and a file an earlier install left there is copied into one of this install's own before use
 * (docs/phone-models.md).
 */
package app.poolhouse.llm.model

import app.poolhouse.llm.engine.CancelToken
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest
import java.util.UUID

private const val PART = ".part"

/** The app's own small record, in storage no other app writes. */
interface Records {
    operator fun get(key: String): String?

    operator fun set(key: String, value: String?)
}

/** A file with a model's size, in a folder the person opened to the app. */
interface Found {
    val bytes: Long

    /** Whether it lies in the app's own models folder, left by an earlier install: it is removed once copied. */
    val left: Boolean

    fun open(): InputStream

    fun remove(): Boolean
}

/** Why the file of a model cannot be used. */
class ModelFileProblem(message: String) : IOException(message)

/** Whether this install may read the file by its path: false for one an earlier install made. */
fun canOpen(file: File): Boolean = runCatching { FileInputStream(file).close() }.isSuccess

class ModelStore(
    val folder: File,
    private val records: Records,
    /** What changes whenever a file's content does: no app can set it back. */
    private val stamp: (File) -> String,
    private val openable: (File) -> Boolean = ::canOpen,
) {
    /** This install's mark, in the names of files that must not meet an earlier install's. */
    private val mark: String
        get() = records["install"] ?: UUID.randomUUID().toString().take(8).also { records["install"] = it }

    /** The names a model's file may have: the published one, else one of this install's when that is taken. */
    private fun names(model: CatalogModel): List<File> {
        val name = model.file.name
        val other = "${name.substringBeforeLast('.')}.$mark.${name.substringAfterLast('.')}"
        return listOf(File(folder, name), File(folder, other))
    }

    private fun whole(model: CatalogModel, file: File) = file.isFile && file.length() == model.bytes

    /** The model's file when it is whole and this install may read it. Its bytes are checked by [verified]. */
    fun file(model: CatalogModel): File? = names(model).firstOrNull { whole(model, it) && openable(it) }

    fun installed(model: CatalogModel): Boolean = file(model) != null

    /** Whether an earlier install left this model here: whole, and not readable until the person opens the folder. */
    fun left(model: CatalogModel): Boolean = !installed(model) && names(model).any { whole(model, it) && !openable(it) }

    /** Where this install downloads or copies a model before it is checked. */
    fun part(model: CatalogModel): File = File(folder, "${model.file.name}.$mark$PART")

    /** The bytes of this model already usable or on their way. */
    fun stored(model: CatalogModel): Long = if (installed(model)) model.bytes else part(model).length().coerceAtMost(model.bytes)

    /** Makes the folder, marked as holding nothing a gallery shows. */
    fun prepare() {
        if (!folder.isDirectory && !folder.mkdirs()) throw ModelFileProblem("The phone did not let the models folder be made.")
        runCatching { File(folder, ".nomedia").createNewFile() }
    }

    private fun key(model: CatalogModel) = "checked:${model.id}"

    private fun seen(model: CatalogModel, file: File) = "${model.file.sha256} ${file.name} ${stamp(file)}"

    /** What names the file as it is now, to notice a change; null without a file. */
    fun version(model: CatalogModel): String? = file(model)?.let(stamp)

    /** Whether the file is still the one whose bytes were last found to be the pinned ones. */
    fun unchanged(model: CatalogModel): Boolean = file(model)?.let { records[key(model)] == seen(model, it) } ?: false

    /** Moves a finished file whose bytes were just checked into place and records it. */
    fun keep(model: CatalogModel, finished: File) {
        val target = names(model).firstOrNull { !it.exists() || openable(it) }
        if (target == null || !finished.renameTo(target)) throw ModelFileProblem("The file of ${model.title} could not be kept.")
        records[key(model)] = seen(model, target)
    }

    /**
     * The file to load, its bytes known to be the pinned ones: hashed again only when it changed since they were last
     * checked, since another app with access to the folder could change it.
     */
    fun verified(model: CatalogModel, cancel: CancelToken = CancelToken()): File {
        val held = file(model) ?: throw ModelFileProblem("${model.title} is not on this phone.")
        // Taken before the bytes are read, so a change made meanwhile is seen at the next check.
        val seen = seen(model, held)
        if (records[key(model)] == seen) return held
        records[key(model)] = null
        val found = held.inputStream().use { digest(it, cancel) } ?: throw ModelFileProblem("Stopped.")
        if (found != model.file.sha256) {
            throw ModelFileProblem("The file of ${model.title} is not the published model any more. Delete it and download it again.")
        }
        records[key(model)] = seen
        return held
    }

    /**
     * Copies a found file into one of this install's own, checking its bytes on the way. False when `cancel` stopped it.
     * The copy is needed because the runtime reads a model by its path, which Android gives only to the file's maker.
     */
    fun adopt(model: CatalogModel, found: Found, cancel: CancelToken, progress: (Long) -> Unit): Boolean {
        if (installed(model)) return true
        if (found.bytes != model.bytes) throw ModelFileProblem("That file does not have the size of ${model.title}.")
        prepare()
        val copy = part(model)
        copy.delete()
        if (freeSpace() < model.bytes) {
            throw ModelFileProblem("Using ${model.title} again needs ${gigabytes(model.bytes)} free for a moment, while it is copied.")
        }
        var held = 0L
        val digest = try {
            found.open().use { from ->
                copy.outputStream().use { to ->
                    digest(from, cancel) { piece, count ->
                        to.write(piece, 0, count)
                        held += count
                        progress(held)
                    }
                }
            }
        } catch (failure: IOException) {
            copy.delete()
            throw failure
        }
        if (digest != model.file.sha256) {
            copy.delete()
            if (digest == null) return false
            throw ModelFileProblem("That file is not the published ${model.title}, so it was not taken.")
        }
        // The app's own earlier file goes first, which frees the published name for this one.
        if (found.left) found.remove()
        keep(model, copy)
        return true
    }

    /** Removes the model's files that this install may remove; false when one an earlier install left remains. */
    fun delete(model: CatalogModel): Boolean {
        for (file in names(model) + part(model)) {
            if (file.exists() && openable(file)) file.delete()
        }
        records[key(model)] = null
        return !left(model)
    }

    fun freeSpace(): Long = generateSequence(folder) { it.parentFile }.first { it.isDirectory }.usableSpace
}

/** The SHA-256 of what `stream` holds, in hex, or null when `cancel` stopped it; `each` is given every piece read. */
fun digest(stream: InputStream, cancel: CancelToken, each: (ByteArray, Int) -> Unit = { _, _ -> }): String? {
    val digest = MessageDigest.getInstance("SHA-256")
    val buffer = ByteArray(1 shl 20)
    while (true) {
        if (cancel.cancelled) return null
        val count = stream.read(buffer)
        if (count < 0) return digest.digest().hex()
        digest.update(buffer, 0, count)
        each(buffer, count)
    }
}

fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }
