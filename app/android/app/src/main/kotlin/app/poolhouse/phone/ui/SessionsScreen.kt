package app.poolhouse.phone.ui

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.poolhouse.core.api.Session
import app.poolhouse.core.device.PluginEntry
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.SessionFilter
import app.poolhouse.core.store.SessionRow
import app.poolhouse.core.store.ago
import app.poolhouse.core.store.deliveryLabel
import app.poolhouse.core.store.needsYou
import app.poolhouse.core.store.sessionRows
import app.poolhouse.core.theme.Section
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.Screen
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.Chips
import app.poolhouse.phone.ui.components.Dot
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.components.SectionLabel
import app.poolhouse.phone.ui.components.Tag
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.phone.ui.theme.Pool
import app.poolhouse.phone.ui.theme.tint

fun kindLabel(kind: String): String = when (kind) {
    "claude" -> "Claude"
    "subagent" -> "Subagent"
    "delegate" -> "Delegate"
    else -> "Agent"
}

fun Session.name(): String = title.ifBlank { agent.ifBlank { id } }

/** Who a session is and where: `claude-db43eb · opus · poolhouse · on Studio`. */
fun Session.who(): String = listOf(
    agent.ifBlank { kindLabel(kind) },
    model,
    project,
    if (thisComputer) "" else device,
).filter { it.isNotBlank() }.joinToString(" · ")

/** The state of a session as a mark: working turns, asking is pink, idle is a dot, ended is a ring. */
@Composable
fun SessionMark(session: Session, modifier: Modifier = Modifier) {
    Box(modifier.size(20.dp).semantics { contentDescription = session.state }, contentAlignment = Alignment.Center) {
        when {
            session.needsYou -> Box(Modifier.size(18.dp).background(Pool.colors.accent, androidx.compose.foundation.shape.CircleShape), contentAlignment = Alignment.Center) {
                Text("?", style = MaterialTheme.typography.labelMedium, color = Pool.colors.onAccent)
            }
            session.state == "working" -> CircularProgressIndicator(Modifier.size(15.dp), strokeWidth = 2.dp, color = Pool.colors.busy)
            session.state == "ended" -> Dot(Pool.colors.lineStrong, hollow = true)
            else -> Dot(Pool.colors.good)
        }
    }
}

/** The first screen: the conversations. With no computer linked they are the phone's own, and linking is offered below them. */
@Composable
fun SessionsScreen(model: PhoneViewModel, state: PoolState?) {
    if (state == null) PhoneChats(model) else AllChats(model, state)
}

@Composable
private fun PhoneChats(model: PhoneViewModel) {
    Column(Modifier.fillMaxSize()) {
        TopBar("Chats", subtitle = "This phone") {
            IconButton({ model.open(Screen.Settings) }) { Icon(Icons.Settings, "Settings", tint = Pool.colors.text) }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            SectionLabel("On this phone")
            PhoneEntries(model)
            LinkOfferCard(model, Modifier.padding(12.dp))
        }
    }
}

/** The rows of the on-device model, or why there are none. */
@Composable
fun PhoneEntries(model: PhoneViewModel) {
    if (model.entries.isEmpty()) {
        Card(Modifier.padding(horizontal = 12.dp)) {
            Text("This build has no model for the phone", style = MaterialTheme.typography.titleSmall)
            Text("Chat on the phone comes with the on-device model module.", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
        }
    }
    for (entry in model.entries) PhoneEntry(entry)
}

@Composable
private fun AllChats(model: PhoneViewModel, state: PoolState) {
    var filter by rememberSaveable { mutableStateOf(SessionFilter.ALL) }
    var searching by rememberSaveable { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    var expanded by remember { mutableStateOf(setOf<String>()) }
    val sessions = state.sessions.value?.sessions
    val rows = remember(sessions, state.seen, expanded, filter, query) {
        sessionRows(sessions ?: emptyList(), state.seen, expanded, filter, query)
    }
    val now = rememberNow()
    Column(Modifier.fillMaxSize()) {
        TopBar("Chats", subtitle = connectionLine(state)) {
            IconButton({ searching = !searching; if (!searching) query = "" }) { Icon(if (searching) Icons.Close else Icons.Search, "Search conversations", tint = Pool.colors.text) }
            IconButton({ model.open(Screen.Settings) }) { Icon(Icons.Settings, "Settings", tint = Pool.colors.text) }
        }
        if (searching) {
            OutlinedTextField(query, { query = it }, placeholder = { Text("Name, agent, project or computer") }, singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp))
        }
        Chips(SessionFilter.entries, filter, { it.label }, Section.CONVERSATIONS.tint(), { filter = it })
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 88.dp)) {
            if (model.entries.isNotEmpty() && filter == SessionFilter.ALL && query.isBlank()) {
                item { SectionLabel("On this phone") }
                items(model.entries, key = { "entry:${it.activity}:${it.title}" }) { PhoneEntry(it) }
                item { SectionLabel("Agents in your pool") }
            }
            val error = state.sessions.error
            if (error.isNotEmpty()) item { Text(error, color = Pool.colors.bad, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(16.dp)) }
            if (sessions == null && error.isEmpty()) {
                item { EmptyState("Reading conversations", "Waiting for ${state.computer.ifEmpty { "the computer" }}.") }
            } else if (rows.isEmpty() && sessions != null) {
                item {
                    if (sessions.isEmpty()) {
                        EmptyState("No agents yet", state.sessions.value?.note?.ifBlank { null }
                            ?: "Claude sessions, their subagents, delegates and other agents the computer sees appear here by themselves.")
                    } else {
                        EmptyState("Nothing here", if (query.isNotBlank()) "No conversation matches “$query”." else "No conversation is ${filter.label.lowercase()}.")
                    }
                }
            }
            items(rows, key = { it.session.id }) { row ->
                SessionRowView(row, now, onOpen = { model.open(Screen.Conversation(row.session.id)) },
                    onUnfold = { lead -> expanded = expanded + lead })
                HorizontalDivider(color = Pool.colors.line, modifier = Modifier.padding(start = (46 + row.depth * 20).dp))
            }
            val note = state.sessions.value?.note.orEmpty()
            if (note.isNotBlank() && rows.isNotEmpty()) item { Text(note, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(16.dp)) }
        }
    }
}

@Composable
private fun PhoneEntry(entry: PluginEntry) {
    val context = LocalContext.current
    Row(
        Modifier.fillMaxWidth().clickable { context.startActivity(Intent().setClassName(context, entry.activity)) }.heightIn(min = 60.dp).padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Phone, contentDescription = null, tint = Section.MODELS.tint(), modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(entry.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(entry.subtitle, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Icon(Icons.Forward, contentDescription = null, tint = Pool.colors.muted, modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun SessionRowView(row: SessionRow, now: Double, onOpen: () -> Unit, onUnfold: (String) -> Unit) {
    val s = row.session
    val line = Pool.colors.line
    val indent = row.depth * 20
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier
                .fillMaxWidth()
                .clickable(onClick = onOpen)
                // A short elbow ties an agent to the lead above it.
                .drawBehind {
                    if (row.depth > 0) {
                        val x = (16 + indent - 12).dp.toPx()
                        val y = 22.dp.toPx()
                        drawLine(line, Offset(x, 0f), Offset(x, y), 1.5.dp.toPx())
                        drawLine(line, Offset(x, y), Offset(x + 8.dp.toPx(), y), 1.5.dp.toPx())
                    }
                }
                .padding(start = (16 + indent).dp, end = 14.dp, top = 11.dp, bottom = 11.dp),
            verticalAlignment = Alignment.Top,
        ) {
            SessionMark(s, Modifier.padding(top = 1.dp))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        s.name(),
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = if (row.unread) FontWeight.Bold else FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(ago(s.activeAt, now), style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
                    if (row.unread) {
                        Spacer(Modifier.width(6.dp))
                        Box(Modifier.semantics { contentDescription = "Unread" }) { Dot(Pool.colors.accent, size = 8.dp) }
                    }
                }
                Text(s.who(), style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                val preview = s.lastText.replace('\n', ' ').ifBlank { s.note }
                if (preview.isNotBlank()) {
                    Text(preview, style = MaterialTheme.typography.bodyMedium, color = if (row.unread) Pool.colors.text else Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                val delivery = deliveryLabel(s.mode)
                if (s.needsYou || delivery.isNotEmpty() || s.state == "ended") {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(top = 3.dp)) {
                        if (s.needsYou) Tag("Needs your answer", Pool.colors.accent, filled = true)
                        if (s.state == "ended") Tag("Finished")
                        if (delivery.isNotEmpty()) Tag(delivery)
                    }
                }
            }
        }
        for (fold in row.folds) {
            Row(
                Modifier.fillMaxWidth().clickable { onUnfold(fold.lead) }.heightIn(min = 40.dp).padding(start = (46 + (fold.depth - 1) * 20).dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Down, contentDescription = null, tint = Pool.colors.muted, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text("Show ${fold.count} finished", style = MaterialTheme.typography.labelMedium, color = Pool.colors.muted)
            }
        }
    }
}
