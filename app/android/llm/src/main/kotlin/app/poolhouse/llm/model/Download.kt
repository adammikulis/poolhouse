/** Fetching a model's file: resumed where it stopped, and kept only when its bytes match the pinned checksum. */
package app.poolhouse.llm.model

import app.poolhouse.llm.engine.CancelToken
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** An answer to a request for a file from `offset`: `resumed` when the body starts there, false when it starts over. */
class Body(val resumed: Boolean, val stream: InputStream, private val onClose: () -> Unit = {}) : AutoCloseable {
    override fun close() {
        try {
            stream.close()
        } finally {
            onClose()
        }
    }
}

fun interface Source {
    fun open(url: String, offset: Long): Body
}

class DownloadFailed(message: String) : IOException(message)

/** The Hub and its file hosts over HTTPS, following redirects that stay on HTTPS. */
object HttpsSource : Source {
    override fun open(url: String, offset: Long): Body {
        var next = URL(url)
        repeat(8) {
            if (next.protocol != "https") throw DownloadFailed("The download left HTTPS.")
            val connection = next.openConnection() as HttpURLConnection
            connection.instanceFollowRedirects = false
            connection.connectTimeout = 20_000
            connection.readTimeout = 60_000
            connection.setRequestProperty("Accept-Encoding", "identity")
            if (offset > 0) connection.setRequestProperty("Range", "bytes=$offset-")
            val status = connection.responseCode
            if (status in listOf(301, 302, 303, 307, 308)) {
                val location = connection.getHeaderField("Location") ?: throw DownloadFailed("The host sent no address to follow.")
                next = URL(next, location)
                connection.disconnect()
                return@repeat
            }
            if (status == 200) return Body(false, connection.inputStream) { connection.disconnect() }
            val range = connection.getHeaderField("Content-Range") ?: ""
            if (status == 206 && range.startsWith("bytes $offset-")) {
                return Body(true, connection.inputStream) { connection.disconnect() }
            }
            connection.disconnect()
            throw DownloadFailed("The download was refused (HTTP $status).")
        }
        throw DownloadFailed("The download was sent on too many times.")
    }
}

class Downloader(private val store: ModelStore, private val source: Source = HttpsSource) {
    /**
     * Brings the file of `model` onto the phone, reporting the bytes held out of [CatalogModel.bytes]. Returns false
     * when `cancel` stopped it, leaving what arrived for the next try.
     */
    fun fetch(model: CatalogModel, cancel: CancelToken, progress: (Long) -> Unit): Boolean {
        if (store.installed(model)) {
            progress(model.bytes)
            return true
        }
        val directory = store.directory(model).apply { mkdirs() }
        val file = model.file
        val finished = File(directory, file.name)
        val part = File(directory, file.name + PART)
        // A whole file with no record of its check is checked again as it stands.
        if (finished.isFile && !part.exists()) finished.renameTo(part)
        if (part.length() > file.bytes) part.delete()
        val digest = MessageDigest.getInstance("SHA-256")
        var held = 0L
        val buffer = ByteArray(1 shl 20)
        if (part.isFile) {
            part.inputStream().use { kept ->
                while (true) {
                    if (cancel.cancelled) return false
                    val count = kept.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                    held += count
                }
            }
        }
        if (held < file.bytes) {
            if (file.bytes - held > directory.usableSpace) {
                throw DownloadFailed("Not enough storage: ${gigabytes(file.bytes - held)} more is needed.")
            }
            source.open(model.url, held).use { body ->
                if (!body.resumed) {
                    digest.reset()
                    held = 0
                }
                val stop = cancel.onCancel { runCatching { body.stream.close() } }
                try {
                    RandomAccessFile(part, "rw").use { out ->
                        out.setLength(held)
                        out.seek(held)
                        while (true) {
                            if (cancel.cancelled) return false
                            val count = body.stream.read(buffer)
                            if (count < 0) break
                            if (held + count > file.bytes) throw DownloadFailed("The host sent more than the pinned size of ${file.name}.")
                            out.write(buffer, 0, count)
                            digest.update(buffer, 0, count)
                            held += count
                            progress(held)
                        }
                    }
                } catch (failure: IOException) {
                    if (cancel.cancelled) return false
                    throw failure
                } finally {
                    stop.close()
                }
            }
        }
        if (cancel.cancelled) return false
        if (held < file.bytes) throw DownloadFailed("The download stopped early. Try again to continue it.")
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (actual != file.sha256) {
            part.delete()
            throw DownloadFailed("${file.name} did not match its checksum, so it was removed. Try again.")
        }
        if (!part.renameTo(finished)) throw DownloadFailed("The downloaded file could not be kept.")
        store.markVerified(model)
        progress(file.bytes)
        return true
    }
}
