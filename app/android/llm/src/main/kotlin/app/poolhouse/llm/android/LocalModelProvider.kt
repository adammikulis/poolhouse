/**
 * Tells a node on the same phone where the lent model answers: one row, readable only by apps signed with this app's key
 * (the signature permission in the manifest). While the switch is off the row says so and gives no address or token.
 */
package app.poolhouse.llm.android

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri

private val COLUMNS = arrayOf("enabled", "url", "token", "model", "context")

class LocalModelProvider : ContentProvider() {
    override fun onCreate(): Boolean = true

    override fun query(uri: Uri, projection: Array<String>?, selection: String?, args: Array<String>?, order: String?): Cursor {
        require(uri.path == "/endpoint") { "There is only /endpoint." }
        val application = requireNotNull(context).applicationContext
        val endpoint = PoolNode.listening(application)
        val model = if (endpoint == null) null else PhoneLender(LocalModels.of(application)).offered()
        return MatrixCursor(COLUMNS).apply {
            addRow(
                arrayOf<Any?>(
                    if (SwitchPreferences(application).enabled) 1 else 0,
                    endpoint?.url,
                    endpoint?.token,
                    model?.id,
                    model?.context,
                ),
            )
            setNotificationUri(application.contentResolver, uri)
        }
    }

    override fun getType(uri: Uri): String = "vnd.android.cursor.item/vnd.poolhouse.localmodel"

    override fun insert(uri: Uri, values: ContentValues?): Uri = throw UnsupportedOperationException("Read only.")

    override fun delete(uri: Uri, selection: String?, args: Array<String>?): Int = throw UnsupportedOperationException("Read only.")

    override fun update(uri: Uri, values: ContentValues?, selection: String?, args: Array<String>?): Int =
        throw UnsupportedOperationException("Read only.")

    companion object {
        fun changed(context: Context) {
            context.contentResolver.notifyChange(Uri.parse("content://${context.packageName}.localmodel/endpoint"), null)
        }
    }
}
