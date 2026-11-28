package app.poolhouse.phone.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.LocalActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.poolhouse.core.store.Connection
import app.poolhouse.core.theme.Section
import app.poolhouse.phone.Linking
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.PoolSession
import app.poolhouse.phone.Screen
import app.poolhouse.phone.Tab
import app.poolhouse.phone.platform.ThemeChoice
import app.poolhouse.phone.ui.components.Banner
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.phone.ui.theme.Pool
import app.poolhouse.phone.ui.theme.PoolhouseTheme
import app.poolhouse.phone.ui.theme.tint
import kotlinx.coroutines.delay

@Composable
fun AppRoot(model: PhoneViewModel) {
    val dark = when (model.theme) {
        ThemeChoice.SYSTEM -> isSystemInDarkTheme()
        ThemeChoice.LIGHT -> false
        ThemeChoice.DARK -> true
    }
    val pool = model.pool
    val onTabs = pool != null && model.linking is Linking.Idle && model.screens.isEmpty()
    val activity = LocalActivity.current as? ComponentActivity
    DisposableEffect(dark, onTabs) {
        val clear = Color.Transparent.toArgb()
        // The bottom bar is navy in both themes, so the navigation icons above it are light.
        activity?.enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.auto(clear, clear) { dark },
            navigationBarStyle = if (onTabs || dark) SystemBarStyle.dark(clear) else SystemBarStyle.light(clear, clear),
        )
        onDispose {}
    }
    PoolhouseTheme(dark) {
        Surface(Modifier.fillMaxSize(), color = Pool.colors.bg, contentColor = Pool.colors.text) {
            val network = localNetwork(needed = pool != null || model.linking !is Linking.Idle)
            Column(Modifier.fillMaxSize().background(Pool.colors.surface).statusBarsPadding().background(Pool.colors.bg)) {
                if (network != null) {
                    Banner("Android blocks the home network until you allow it.", Pool.colors.accentDim, Pool.colors.text, "Allow", network)
                }
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    val linking = model.linking
                    when {
                        linking is Linking.Scanning -> ScanScreen(onCode = model::opened, onClose = model::cancelLinking)
                        linking is Linking.Working -> LinkingScreen(linking.computer, model::cancelLinking)
                        linking is Linking.Confirm -> ConfirmScreen(linking.link, model::confirmed)
                        pool == null -> LinkScreen(model, (linking as? Linking.Idle)?.error ?: "")
                        else -> Home(model, pool)
                    }
                }
            }
            val replace = model.linking as? Linking.Replace
            if (replace != null && pool != null) {
                AlertDialog(
                    onDismissRequest = model::cancelLinking,
                    title = { Text("Link another computer?") },
                    text = { Text("The phone is linked to ${pool.link.computer}. Linking ${replace.offer.computer.ifEmpty { "the new computer" }} replaces that link.") },
                    confirmButton = { TextButton({ model.link(replace.offer, model.defaultName()) }) { Text("Replace") } },
                    dismissButton = { TextButton(model::cancelLinking) { Text("Keep") } },
                )
            }
        }
    }
}

/** Asks for the local network once it is needed (Android 17 and later). Null when nothing is missing, else the way to ask again. */
@Composable
private fun localNetwork(needed: Boolean): (() -> Unit)? {
    if (Build.VERSION.SDK_INT < 37) return null
    val context = LocalContext.current
    var granted by remember { mutableStateOf(context.checkSelfPermission(Manifest.permission.ACCESS_LOCAL_NETWORK) == PackageManager.PERMISSION_GRANTED) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(needed, granted) {
        if (needed && !granted) ask.launch(Manifest.permission.ACCESS_LOCAL_NETWORK)
    }
    return if (needed && !granted) ({ ask.launch(Manifest.permission.ACCESS_LOCAL_NETWORK) }) else null
}

fun Tab.section(): Section = when (this) {
    Tab.CHATS -> Section.CONVERSATIONS
    Tab.POOL -> Section.POOL
    Tab.WORK -> Section.WORK
    Tab.INBOX -> Section.MODELS
}

@Composable
private fun Home(model: PhoneViewModel, pool: PoolSession) {
    val state by pool.store.state.collectAsStateWithLifecycle()
    val connection = state.connection
    if (connection is Connection.Unlinked) {
        EmptyState("This phone was removed", "${connection.why} Link it again to carry on.", action = "Link again", onAction = model::unlink)
        return
    }
    var directing by remember { mutableStateOf(false) }
    BackHandler(model.screens.isNotEmpty() || model.tab != Tab.CHATS) {
        if (model.screens.isNotEmpty()) model.back() else model.show(Tab.CHATS)
    }
    val top = model.screens.lastOrNull()
    Column(Modifier.fillMaxSize().then(if (top == null) Modifier else Modifier.navigationBarsPadding().imePadding())) {
        if (state.canned) Banner("CANNED SAMPLE DATA: this is a test server, not your pool.", Color(Section.WORK.color), Color(0xFF1B1F3A))
        if (connection is Connection.Offline) {
            Banner("${connection.why} Showing what was last read.", Pool.colors.accentDim, Pool.colors.text, "Settings") { model.open(Screen.Settings) }
        } else if (connection is Connection.Connecting) {
            Banner("Connecting to ${state.computer}…", Pool.colors.sunken, Pool.colors.muted)
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (top) {
                null -> when (model.tab) {
                    Tab.CHATS -> SessionsScreen(model, pool, state)
                    Tab.POOL -> PoolScreen(model, pool, state)
                    Tab.WORK -> WorkScreen(model, state)
                    Tab.INBOX -> InboxScreen(model, state)
                }
                is Screen.Conversation -> ConversationScreen(model, pool, state, top.session)
                is Screen.Device -> DeviceScreen(model, pool, state, top.id)
                is Screen.Work -> WorkDetailScreen(model, pool, state, top.kind, top.id)
                is Screen.Settings -> SettingsScreen(model, pool, state)
            }
            if (top == null && model.tab != Tab.INBOX) {
                ExtendedFloatingActionButton(
                    onClick = { directing = true },
                    containerColor = model.tab.section().tint(),
                    contentColor = Color(0xFF1B1F3A),
                    // The button's words are not read out on their own: the icon names it for a screen reader.
                    icon = { Icon(Icons.Plus, contentDescription = "Direct") },
                    text = { Text("Direct") },
                    modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
                )
            }
            if (state.notice.isNotEmpty()) {
                LaunchedEffect(state.notice) {
                    delay(4_000)
                    pool.store.clearNotice()
                }
                Snackbar(
                    modifier = Modifier.align(Alignment.BottomCenter).padding(start = 12.dp, end = 12.dp, bottom = if (top == null) 84.dp else 96.dp),
                    action = { TextButton(pool.store::clearNotice) { Text("OK", color = Pool.colors.accent) } },
                ) { Text(state.notice) }
            }
        }
        if (top == null) BottomBar(model.tab, state.inbox.value?.questions?.size ?: 0, model::show)
    }
    if (directing) DirectSheet(model, pool, state, onClose = { directing = false })
}
