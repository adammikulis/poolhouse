package app.poolhouse.phone.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.poolhouse.core.store.PoolState
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.PoolSession
import app.poolhouse.theme.ThemeChoice
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.Chips
import app.poolhouse.phone.ui.components.Fact
import app.poolhouse.phone.ui.components.SecondaryButton
import app.poolhouse.phone.ui.components.SectionLabel
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.theme.Pool

@Composable
fun SettingsScreen(model: PhoneViewModel, pool: PoolSession?, state: PoolState?) {
    var unlinking by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val version = remember { context.packageManager.getPackageInfo(context.packageName, 0).versionName.orEmpty() }
    Column(Modifier.fillMaxSize()) {
        TopBar("Settings", subtitle = state?.let(::connectionLine) ?: "This phone", onBack = model::back)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            ThisPhoneCard(model, pool, state)
            if (pool == null) {
                LinkOfferCard(model)
            } else {
                Card {
                    Text("Linked computer", style = MaterialTheme.typography.titleSmall)
                    Fact("Name", state?.computer.orEmpty().ifBlank { pool.link.computer })
                    Fact("Reached at", pool.link.addresses.joinToString("\n"), mono = true)
                    Fact("This phone", state?.me?.value?.name.orEmpty())
                    Fact("Key words", pool.link.words, mono = true)
                    Text("The computer's Phones screen shows the same words for this phone.", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
                    SecondaryButton("Unlink this phone", { unlinking = true }, Modifier.fillMaxWidth().padding(top = 8.dp), danger = true)
                }
            }
            if (model.entries.isNotEmpty()) {
                SectionLabel("Models on this phone")
                PhoneEntries(model)
            }
            Card {
                Text("Appearance", style = MaterialTheme.typography.titleSmall)
                Chips(ThemeChoice.entries, model.theme, { it.label }, Pool.colors.accent, model::choose, inset = 0.dp)
            }
            Card {
                Fact("App version", version)
                Text(
                    if (pool == null) "With no computer linked, the app talks to no computer. It stores no conversation of a computer's agents."
                    else "The app talks only to the linked computer, over a connection pinned to its certificate. It stores no conversation: closing the app forgets what was read.",
                    style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted,
                )
            }
        }
    }
    if (unlinking && pool != null) {
        AlertDialog(
            onDismissRequest = { unlinking = false },
            title = { Text("Unlink this phone?") },
            text = { Text("The phone forgets ${pool.link.computer} and the computer forgets the phone. Link again with a new code.") },
            confirmButton = { TextButton({ unlinking = false; model.unlink() }) { Text("Unlink", color = Pool.colors.bad) } },
            dismissButton = { TextButton({ unlinking = false }) { Text("Keep") } },
        )
    }
}
