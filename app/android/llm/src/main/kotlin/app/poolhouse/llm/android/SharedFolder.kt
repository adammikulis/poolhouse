/**
 * Where the models lie on the phone: a hidden folder in Download. An app makes it and fills it with no permission, no
 * file list or gallery shows it, and it stays when the app is removed. A later install reads the files left there only
 * after the person opens the folder to it in the system's picker (docs/phone-models.md).
 */
package app.poolhouse.llm.android

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import android.system.Os
import app.poolhouse.llm.model.CatalogModel
import app.poolhouse.llm.model.Found
import app.poolhouse.llm.model.ModelStore
import app.poolhouse.llm.model.Records
import java.io.File
import java.io.IOException
import java.io.InputStream

private const val FOLDER = ".models"
private const val PROVIDER = "com.android.externalstorage.documents"

/** The models folder as the system's picker names it. */
private const val FOLDER_ID = "primary:Download/$FOLDER"

fun phoneStore(context: Context): ModelStore {
    val held = context.getSharedPreferences("poolhouse-llm-files", Context.MODE_PRIVATE)
    val records = object : Records {
        override fun get(key: String): String? = held.getString(key, null)

        override fun set(key: String, value: String?) = held.edit().putString(key, value).apply()
    }
    val downloads = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
    return ModelStore(File(downloads, FOLDER), records, ::changeStamp)
}

/** The file's identity and the times of its last changes; the second time the system sets itself on every change. */
private fun changeStamp(file: File): String = Os.stat(file.path).let {
    "${it.st_ino} ${it.st_size} ${it.st_mtim.tv_sec}.${it.st_mtim.tv_nsec} ${it.st_ctim.tv_sec}.${it.st_ctim.tv_nsec}"
}

/**
 * The system's folder picker. Opened on the models folder, reusing the files left there takes two taps ("Use this
 * folder", "Allow"); with `elsewhere` the person goes to a folder of their own.
 */
fun folderPicker(elsewhere: Boolean): Intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
    if (!elsewhere) putExtra(DocumentsContract.EXTRA_INITIAL_URI, DocumentsContract.buildDocumentUri(PROVIDER, FOLDER_ID))
}

/** The files in the opened folder that have the size of `model`'s file, whatever their names. */
fun found(context: Context, tree: Uri, model: CatalogModel): List<Found> {
    val root = DocumentsContract.getTreeDocumentId(tree)
    val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, root)
    val columns = arrayOf(Document.COLUMN_DOCUMENT_ID, Document.COLUMN_SIZE)
    val matching = mutableListOf<String>()
    val rows = context.contentResolver.query(children, columns, null, null, null) ?: throw IOException("The folder could not be read.")
    rows.use { while (it.moveToNext()) if (it.getLong(1) == model.bytes) matching.add(it.getString(0)) }
    return matching.map { id -> Opened(context, DocumentsContract.buildDocumentUriUsingTree(tree, id), model.bytes, root == FOLDER_ID) }
}

private class Opened(private val context: Context, private val uri: Uri, override val bytes: Long, override val left: Boolean) : Found {
    override fun open(): InputStream = context.contentResolver.openInputStream(uri) ?: throw IOException("The file could not be opened.")

    override fun remove(): Boolean = runCatching { DocumentsContract.deleteDocument(context.contentResolver, uri) }.getOrDefault(false)
}
