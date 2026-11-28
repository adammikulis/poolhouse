package app.poolhouse.phone.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.poolhouse.core.store.MESSAGE_LIMIT
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.canSend
import app.poolhouse.core.store.deliveryLabel
import app.poolhouse.core.theme.Section
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.PoolSession
import app.poolhouse.phone.Screen
import app.poolhouse.phone.ui.components.Chips
import app.poolhouse.phone.ui.components.PrimaryButton
import app.poolhouse.phone.ui.components.SecondaryButton
import app.poolhouse.phone.ui.components.SectionLabel
import app.poolhouse.theme.Pool
import app.poolhouse.theme.tint

/** Directing from one place under the thumb: tell an agent something, serve or stop a model, pause or resume the pool. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DirectSheet(model: PhoneViewModel, pool: PoolSession, state: PoolState, onClose: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onClose, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = Pool.colors.bg) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(bottom = 12.dp)) {
            Text("Direct", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(horizontal = 16.dp))

            SectionLabel("Tell an agent")
            // The sessions that take a message now come first: a lead before its agents, then the most recently active.
            val reachable = state.sessions.value?.sessions.orEmpty().filter { it.canSend }
                .sortedWith(compareBy<app.poolhouse.core.api.Session> { it.mode != "wake" && it.mode != "next_step" }.thenBy { it.parent != null }.thenByDescending { it.activeAt })
            if (reachable.isEmpty()) {
                Text("No agent can take a message now.", style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted, modifier = Modifier.padding(horizontal = 16.dp))
            }
            for (session in reachable.take(4)) {
                Row(
                    Modifier.fillMaxWidth().clickable { onClose(); model.open(Screen.Conversation(session.id)) }.heightIn(min = 52.dp).padding(horizontal = 16.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SessionMark(session)
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text(session.name(), style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(listOf(session.agent, deliveryLabel(session.mode).ifBlank { "Delivered now" }).filter { it.isNotBlank() }.joinToString(" · "),
                            style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1)
                    }
                }
            }

            SectionLabel("Start a delegate")
            StartDelegate(pool, state) { name ->
                onClose()
                model.open(Screen.Conversation("delegate-$name"))
            }

            SectionLabel("Models")
            Column(Modifier.padding(horizontal = 12.dp)) { ModelsCard(pool, state) }

            SectionLabel("Pool")
            val paused = state.devices.value?.devices.orEmpty().any { it.paused }
            Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val busy = "pool" in state.busy
                if (paused) SecondaryButton("Resume the pool", { pool.store.resumePool() }, Modifier.weight(1f), working = busy)
                else {
                    SecondaryButton("Pause 1 h", { pool.store.pausePool("1h") }, Modifier.weight(1f), working = busy)
                    SecondaryButton("Pause 8 h", { pool.store.pausePool("8h") }, Modifier.weight(1f), working = busy)
                }
            }
        }
    }
}

/** A local delegate on one of the computer's projects: the task in the person's words, and whether it may change files. */
@Composable
private fun StartDelegate(pool: PoolSession, state: PoolState, started: (String) -> Unit) {
    LaunchedEffect(Unit) { pool.store.read("projects") }
    var project by rememberSaveable { mutableStateOf("") }
    var task by rememberSaveable { mutableStateOf("") }
    var edit by rememberSaveable { mutableStateOf(false) }
    val projects = state.projects.value?.projects
    Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (state.projects.error.isNotEmpty()) Text(state.projects.error, style = MaterialTheme.typography.bodySmall, color = Pool.colors.bad)
        if (projects == null) {
            if (state.projects.error.isEmpty()) Text("Reading the computer's projects…", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
            return@Column
        }
        if (projects.isEmpty()) {
            Text("The computer knows no project yet.", style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted)
            return@Column
        }
        val chosen = projects.firstOrNull { it.id == project } ?: projects.first()
        Chips(projects, chosen, { it.name.ifBlank { it.path } }, Section.WORK.tint(), { project = it.id }, inset = 0.dp)
        OutlinedTextField(task, { if (it.length <= MESSAGE_LIMIT) task = it }, placeholder = { Text("What should it do?") }, modifier = Modifier.fillMaxWidth(), minLines = 2, maxLines = 6)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("May change files", style = MaterialTheme.typography.bodyLarge)
                Text(if (edit) "It works in the project and can edit it." else "It only reads and answers.", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
            }
            Switch(edit, { edit = it }, colors = SwitchDefaults.colors(checkedTrackColor = Pool.colors.accent, checkedThumbColor = Pool.colors.onAccent))
        }
        PrimaryButton("Start", { pool.store.startDelegate(chosen.id, task, edit, "") { name -> task = ""; started(name) } }, Modifier.fillMaxWidth(),
            enabled = task.isNotBlank(), working = "start" in state.busy)
    }
}
