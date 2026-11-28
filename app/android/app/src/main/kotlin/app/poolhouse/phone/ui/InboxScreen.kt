package app.poolhouse.phone.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.ago
import app.poolhouse.core.store.until
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.Screen
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.components.SectionLabel
import app.poolhouse.phone.ui.components.Tag
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.theme.Pool

/** What waits for the person and the newest notices: questions first, each one tap from its conversation. */
@Composable
fun InboxScreen(model: PhoneViewModel, state: PoolState) {
    val inbox = state.inbox.value
    val now = rememberNow()
    fun title(id: String) = state.sessions.value?.sessions?.firstOrNull { it.id == id }?.name() ?: id
    Column(Modifier.fillMaxSize()) {
        TopBar("Inbox", subtitle = connectionLine(state))
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 24.dp)) {
            if (state.inbox.error.isNotEmpty()) item { Text(state.inbox.error, style = MaterialTheme.typography.bodySmall, color = Pool.colors.bad, modifier = Modifier.padding(16.dp)) }
            val questions = inbox?.questions.orEmpty()
            val notices = inbox?.notices.orEmpty().sortedByDescending { it.at }
            if (inbox == null) {
                if (state.inbox.error.isEmpty()) item { EmptyState("Reading", "Waiting for ${state.computer.ifEmpty { "the computer" }}.") }
            } else if (questions.isEmpty() && notices.isEmpty()) {
                item { EmptyState("Nothing waits for you", "An agent's questions and notices appear here.") }
            }
            if (questions.isNotEmpty()) item { SectionLabel("Waiting for you") }
            items(questions, key = { "q:${it.session}:${it.question}" }) { q ->
                Card(Modifier.padding(horizontal = 12.dp, vertical = 5.dp), onClick = { model.open(Screen.Conversation(q.session)) }) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Tag("Question", Pool.colors.accent, filled = true)
                        Spacer(Modifier.width(8.dp))
                        Text(title(q.session), style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1, modifier = Modifier.weight(1f))
                        val left = until(q.expiresAt, now)
                        if (left.isNotEmpty()) Text("ends $left", style = MaterialTheme.typography.bodySmall, color = Pool.colors.warn)
                    }
                    for (one in q.questions) Text(one.question, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
                    Text("Answer", style = MaterialTheme.typography.labelLarge, color = Pool.colors.accentInk, modifier = Modifier.padding(top = 8.dp))
                }
            }
            if (notices.isNotEmpty()) item { SectionLabel("Notices") }
            items(notices, key = { "n:${it.session}:${it.id}" }) { notice ->
                Column {
                    Row(Modifier.fillMaxWidth().clickable(enabled = notice.session.isNotEmpty()) { model.open(Screen.Conversation(notice.session)) }.padding(horizontal = 16.dp, vertical = 10.dp)) {
                        val warn = notice.kind == "permission"
                        Icon(if (warn) Icons.Alert else Icons.Bell, null, tint = if (warn) Pool.colors.warn else Pool.colors.muted, modifier = Modifier.padding(top = 2.dp).size(18.dp))
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(notice.text, style = MaterialTheme.typography.bodyMedium)
                            if (notice.session.isNotEmpty()) Text(title(notice.session), style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1)
                        }
                        Spacer(Modifier.width(8.dp))
                        Text(ago(notice.at, now), style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
                    }
                    HorizontalDivider(color = Pool.colors.line)
                }
            }
        }
    }
}
