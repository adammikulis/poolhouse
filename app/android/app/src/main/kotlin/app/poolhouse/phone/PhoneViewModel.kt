package app.poolhouse.phone

import android.Manifest
import android.app.Application
import android.content.pm.PackageManager
import android.os.Build
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.poolhouse.core.device.ComputerLink
import app.poolhouse.core.device.PLUGIN_CLASS
import app.poolhouse.core.device.PhonePlugin
import app.poolhouse.core.device.PhoneWorker
import app.poolhouse.core.device.PluginEntry
import app.poolhouse.core.link.Address
import app.poolhouse.core.link.LinkOffer
import app.poolhouse.core.link.cleanName
import app.poolhouse.core.link.linkPhone
import app.poolhouse.core.net.Link
import app.poolhouse.core.net.PhoneApi
import app.poolhouse.core.net.PhoneClient
import app.poolhouse.core.net.PhoneError
import app.poolhouse.core.store.PoolStore
import app.poolhouse.core.store.WorkKind
import app.poolhouse.phone.platform.KeystoreKey
import app.poolhouse.phone.platform.Prefs
import app.poolhouse.theme.ThemeChoice
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.plus
import kotlinx.coroutines.withTimeoutOrNull

enum class Tab(val label: String) { CHATS("Chats"), POOL("Pool"), WORK("Work"), INBOX("Inbox") }

/** A screen above a tab. */
sealed interface Screen {
    data class Conversation(val session: String) : Screen

    data class Device(val id: String) : Screen

    data class Work(val kind: WorkKind, val id: String) : Screen

    data object Settings : Screen
}

/** Where linking a computer stands. Linking is offered, never required: [Off] is the app itself, with or without a computer. */
sealed interface Linking {
    data object Off : Linking

    /** The person asked to link and is choosing how; `error` says why the last try did not work. */
    data class Choosing(val error: String = "") : Linking

    data object Scanning : Linking

    data class Working(val computer: String) : Linking

    /** Android must be allowed to use the home network before the computer can be reached. */
    data class Permission(val offer: LinkOffer, val name: String) : Linking

    /** Linked: the key's words, to compare with the computer's Phones screen. */
    data class Confirm(val link: Link) : Linking

    /** A code was opened while a computer is linked: replacing it is asked first. */
    data class Replace(val offer: LinkOffer) : Linking
}

/** Everything of one link: the client, the calls and the store, ended together when the link ends. */
class PoolSession(val link: Link, val api: PhoneApi, val store: PoolStore, private val job: Job) {
    fun end() {
        store.pause()
        job.cancel()
    }
}

class PhoneViewModel(application: Application) : AndroidViewModel(application) {
    private val prefs = Prefs(application)
    private val plugin: PhonePlugin? =
        runCatching { Class.forName(PLUGIN_CLASS).getDeclaredConstructor().newInstance() as PhonePlugin }.getOrNull()

    /** The model module's way to a computer: the linked one's, or a refusal while none is linked. */
    private val computer = ComputerLink { op, body, readTimeoutMs ->
        pool?.api?.call(op, body, readTimeoutMs) ?: throw PhoneError.Unreachable("No computer is linked.")
    }

    var pool: PoolSession? by mutableStateOf(null)
        private set

    /** The on-device model's switch, when this build has the model, with or without a computer. */
    val worker: PhoneWorker? = plugin?.worker(application, computer)

    /** The screens the on-device model offers, listed with the conversations. */
    var entries: List<PluginEntry> by mutableStateOf(plugin?.entries(application) ?: emptyList())
        private set

    var linking: Linking by mutableStateOf(Linking.Off)
        private set

    /** Something that happened to the link, shown once on the app's own screens. */
    var notice: String by mutableStateOf("")
        private set
    var theme: ThemeChoice by mutableStateOf(prefs.theme)
        private set
    var tab: Tab by mutableStateOf(Tab.CHATS)
        private set
    val screens = mutableStateListOf<Screen>()

    private var onScreen = false
    private var attempt: Job? = null

    init {
        val link = prefs.link
        val key = if (link == null) null else KeystoreKey.existing()
        if (link != null && key != null) pool = session(link, key)
    }

    private fun session(link: Link, key: KeystoreKey): PoolSession {
        val job = SupervisorJob()
        val scope = viewModelScope + job
        val client = PhoneClient(link, key)
        val api = PhoneApi(client)
        val store = PoolStore(api, client::hello, scope, prefs, link.computer, onPoolUseChanged = { worker?.refresh() })
        return PoolSession(link, api, store, job).also { if (onScreen) store.resume() }
    }

    /** The app came on screen or left it: the computer is followed only while someone looks. */
    fun visible(now: Boolean) {
        onScreen = now
        if (now) {
            // A model downloaded or removed on the module's screens changes what the rows say.
            entries = plugin?.entries(getApplication()) ?: emptyList()
            if (pool != null) worker?.refresh()
            pool?.store?.resume()
        } else {
            pool?.store?.pause()
        }
    }

    fun show(next: Tab) {
        screens.clear()
        tab = next
    }

    fun open(screen: Screen) {
        if (screens.lastOrNull() != screen) screens.add(screen)
    }

    fun back() {
        screens.removeLastOrNull()
    }

    fun choose(choice: ThemeChoice) {
        theme = choice
        prefs.theme = choice
    }

    fun offerLink() {
        linking = Linking.Choosing()
    }

    fun scan() {
        linking = Linking.Scanning
    }

    /** Leaves linking for the app; a reason keeps the choosing screen open to show it. */
    fun cancelLinking(why: String = "") {
        attempt?.cancel()
        linking = if (why.isEmpty()) Linking.Off else Linking.Choosing(why)
    }

    fun clearNotice() {
        notice = ""
    }

    /** A code from the camera or from a link the person opened. */
    fun opened(text: String) {
        if (linking is Linking.Working) return
        LinkOffer.parse(text).fold(
            onSuccess = { offer -> if (pool == null) link(offer, defaultName()) else linking = Linking.Replace(offer) },
            onFailure = { linking = Linking.Choosing(it.message ?: "This is not a Poolhouse link code.") },
        )
    }

    fun typed(address: String, code: String, name: String) {
        LinkOffer.typed(address, code).fold(
            onSuccess = { link(it, name) },
            onFailure = { linking = Linking.Choosing(it.message ?: "Check the address and the code.") },
        )
    }

    fun defaultName(): String = cleanName(Build.MODEL ?: "")

    /** Whether Android (17 and later) still blocks an address of these: every one but the phone's own loopback needs the permission. */
    fun blocked(addresses: List<Address>): Boolean =
        Build.VERSION.SDK_INT >= 37 && addresses.any { !it.loopback } &&
            getApplication<Application>().checkSelfPermission(Manifest.permission.ACCESS_LOCAL_NETWORK) != PackageManager.PERMISSION_GRANTED

    fun link(offer: LinkOffer, name: String) {
        attempt?.cancel()
        if (blocked(offer.addresses)) {
            linking = Linking.Permission(offer, name)
            return
        }
        linking = Linking.Working(offer.computer.ifEmpty { offer.addresses.first().toString() })
        attempt = viewModelScope.launch {
            linking = try {
                // A link being replaced stops lending the phone before it is forgotten.
                if (pool != null) worker?.setEnabled(false)
                forget()
                val link = linkPhone(offer, name.ifBlank { defaultName() }, KeystoreKey.create())
                prefs.link = link
                Linking.Confirm(link)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                KeystoreKey.delete()
                Linking.Choosing(error.message ?: "The phone could not be linked.")
            }
        }
    }

    /** The person compared the words: the app opens on the computer's conversations. */
    fun confirmed() {
        val link = prefs.link ?: return cancelLinking()
        val key = KeystoreKey.existing() ?: return cancelLinking()
        pool = session(link, key)
        linking = Linking.Off
        worker?.refresh()
        show(Tab.CHATS)
    }

    private fun forget() {
        pool?.end()
        pool = null
        screens.clear()
        prefs.link = null
        KeystoreKey.delete()
    }

    /** Ends the link: the computer is told when it can be reached, and the phone forgets it either way. */
    fun unlink() {
        val current = pool ?: return
        worker?.setEnabled(false)
        viewModelScope.launch {
            val told = withTimeoutOrNull(8_000) { runCatching { current.api.unlink() }.isSuccess } == true
            forget()
            show(Tab.CHATS)
            notice = if (told) "" else "Removed from this phone. The computer could not be told: remove the phone on its Phones screen too."
        }
    }

    /** The computer no longer knows the phone: the link is forgotten and the app carries on without it. */
    fun removedByComputer(why: String) {
        worker?.setEnabled(false)
        forget()
        notice = why.ifBlank { "The computer removed this phone." }
    }

    override fun onCleared() {
        pool?.end()
    }
}
