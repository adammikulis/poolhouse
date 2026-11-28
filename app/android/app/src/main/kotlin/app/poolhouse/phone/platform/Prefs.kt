package app.poolhouse.phone.platform

import android.content.Context
import app.poolhouse.core.link.Address
import app.poolhouse.core.net.Link
import app.poolhouse.core.store.SeenStore
import app.poolhouse.theme.ThemeChoice
import app.poolhouse.theme.ThemePreference
import org.json.JSONArray
import org.json.JSONObject

/**
 * What the app keeps on the phone: the link (no secret: the key stays in the Keystore), what has been seen, and the theme. The app
 * is excluded from backups.
 */
class Prefs(private val context: Context) : SeenStore {
    private val store = context.getSharedPreferences("poolhouse", Context.MODE_PRIVATE)

    var link: Link?
        get() = runCatching {
            val o = JSONObject(store.getString("link", null) ?: return null)
            val addresses = o.getJSONArray("addresses")
            Link(
                phone = o.getString("phone"),
                pin = o.getString("pin"),
                computer = o.getString("computer"),
                words = o.getString("words"),
                addresses = (0 until addresses.length()).mapNotNull { Address.parse(addresses.getString(it)) },
                clockOffsetMs = o.optLong("clockOffsetMs"),
            )
        }.getOrNull()
        set(value) {
            val edit = store.edit()
            if (value == null) {
                edit.remove("link").remove("seen")
            } else {
                val o = JSONObject().put("phone", value.phone).put("pin", value.pin).put("computer", value.computer).put("words", value.words)
                    .put("addresses", JSONArray(value.addresses.map { it.authority })).put("clockOffsetMs", value.clockOffsetMs)
                edit.putString("link", o.toString())
            }
            edit.apply()
        }

    var theme: ThemeChoice
        get() = ThemePreference.read(context)
        set(value) = ThemePreference.write(context, value)

    override fun load(): Map<String, Double> = runCatching {
        val o = JSONObject(store.getString("seen", "{}")!!)
        o.keys().asSequence().associateWith { o.getDouble(it) }
    }.getOrDefault(emptyMap())

    override fun save(seen: Map<String, Double>) {
        store.edit().putString("seen", JSONObject(seen as Map<*, *>).toString()).apply()
    }
}
