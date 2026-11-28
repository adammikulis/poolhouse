package app.poolhouse.phone.ui

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.poolhouse.core.api.Session
import app.poolhouse.core.store.Delivery
import app.poolhouse.core.store.Item
import app.poolhouse.core.store.MESSAGE_LIMIT
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.canSend
import app.poolhouse.core.store.deliveryLabel
import app.poolhouse.core.store.items
import app.poolhouse.core.store.openQuestions
import app.poolhouse.core.store.until
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.PoolSession
import app.poolhouse.phone.Screen
import app.poolhouse.phone.ui.components.CodeBlock
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.components.Markdown
import app.poolhouse.phone.ui.components.PrimaryButton
import app.poolhouse.phone.ui.components.Tag
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.theme.Pool

private val stateWords = mapOf("working" to "working", "idle" to "idle", "asking" to "waiting for your answer", "ended" to "finished")

/** One conversation with one agent: its history, live as the computer streams it, its questions and the send box. */
@Composable
fun ConversationScreen(model: PhoneViewModel, pool: PoolSession, state: PoolState, id: String) {
    val session = state.sessions.value?.sessions?.firstOrNull { it.id == id }
    DisposableEffect(id) {
        pool.store.openSession(id)
        onDispose { pool.store.closeSession(id) }
    }
    val transcript = state.transcripts[id]
    val shown = remember(transcript) { transcript?.let(::items) ?: emptyList() }
    val now = rememberNow(5_000)
    val asks = openQuestions(shown, now)
    val list = rememberLazyListState()
    // The list is bottom-anchored: it stays on the newest entry while the person reads the end of it.
    LaunchedEffect(shown.size) {
        if (list.firstVisibleItemIndex <= 1) list.scrollToItem(0)
    }
    Column(Modifier.fillMaxSize()) {
        TopBar(
            session?.name() ?: "Conversation",
            subtitle = session?.let { listOf(stateWords[it.state] ?: it.state, it.who()).joinToString(" · ") } ?: "",
            onBack = model::back,
        ) {
            if (session != null) SessionMark(session, Modifier.padding(end = 4.dp))
            // A delegate has a stop; a Claude session is stopped by asking it (docs/phone-api.md).
            if (session?.kind == "delegate" && session.state != "ended") {
                IconButton({ pool.store.stopSession(id) }, enabled = "stop:$id" !in state.busy) { Icon(Icons.Stop, "Stop this delegate", tint = Pool.colors.bad) }
            }
        }
        if (session?.parent != null) {
            val parent = state.sessions.value?.sessions?.firstOrNull { it.id == session.parent }
            if (parent != null) {
                Row(Modifier.fillMaxWidth().clickable { model.open(Screen.Conversation(parent.id)) }.background(Pool.colors.sunken).padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Lead: ${parent.name()}", style = MaterialTheme.typography.labelMedium, color = Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Icon(Icons.Forward, null, tint = Pool.colors.muted, modifier = Modifier.size(16.dp))
                }
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (shown.isEmpty()) {
                val error = transcript?.error.orEmpty()
                when {
                    error.isNotEmpty() -> EmptyState("History not read", error, action = "Try again", onAction = pool.store::refresh)
                    transcript?.loaded != true -> CircularProgressIndicator(Modifier.align(Alignment.Center), color = Pool.colors.accent)
                    else -> EmptyState("Nothing yet", "What this agent is told and what it says appear here.")
                }
            } else {
                LazyColumn(Modifier.fillMaxSize(), state = list, reverseLayout = true, contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.Bottom)) {
                    if (session?.state == "working") item(key = "working") { Working() }
                    items(shown.asReversed(), key = { it.key }) { item ->
                        Entry(item, now, onRetry = { pool.store.retry(id, it) }, onDiscard = { pool.store.discard(id, it) })
                    }
                }
            }
        }
        HorizontalDivider(color = Pool.colors.line)
        val ask = asks.firstOrNull()
        if (ask != null) {
            QuestionDock(ask, now, asks.size, busy = "answer:${ask.question}" in state.busy) { answers -> pool.store.answer(id, ask.question, answers) }
        } else if (session != null) {
            Composer(session) { pool.store.send(id, it) }
        }
    }
}

@Composable
private fun Working() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(Modifier.size(13.dp), strokeWidth = 2.dp, color = Pool.colors.busy)
        Spacer(Modifier.width(8.dp))
        Text("Working…", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
    }
}

@Composable
private fun Entry(item: Item, now: Double, onRetry: (String) -> Unit, onDiscard: (String) -> Unit) {
    when (item) {
        is Item.Prompt -> Mine(item.text, when (item.from) { "lead" -> "From the lead"; "phone" -> "From this phone"; else -> "From the computer" }, dim = item.from == "lead")
        is Item.Outgoing -> Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
            Mine(item.text, "", dim = item.delivery != Delivery.DELIVERED)
            val status = when (item.delivery) {
                Delivery.SENDING -> "Sending…"
                Delivery.WAITING -> item.detail.ifBlank { "Waiting to be delivered" }
                Delivery.DELIVERED -> "Delivered"
                Delivery.DROPPED -> "Not delivered. ${item.detail}"
                Delivery.FAILED -> "Not sent. ${item.detail}"
            }
            val bad = item.delivery == Delivery.DROPPED || item.delivery == Delivery.FAILED
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 3.dp)) {
                if (item.delivery == Delivery.DELIVERED) {
                    Icon(Icons.Check, null, tint = Pool.colors.good, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(4.dp))
                } else if (item.delivery == Delivery.WAITING) {
                    Icon(Icons.Clock, null, tint = Pool.colors.warn, modifier = Modifier.size(14.dp))
                    Spacer(Modifier.width(4.dp))
                }
                Text(status, style = MaterialTheme.typography.bodySmall, color = if (bad) Pool.colors.bad else Pool.colors.muted,
                    textAlign = TextAlign.End, modifier = Modifier.widthIn(max = 280.dp))
                if (item.delivery == Delivery.FAILED && item.draft.isNotEmpty()) {
                    TextAction("Retry") { onRetry(item.draft) }
                    TextAction("Discard") { onDiscard(item.draft) }
                }
            }
        }
        is Item.Reply -> Markdown(item.text, Modifier.fillMaxWidth())
        is Item.Steps -> Steps(item)
        is Item.Ask -> Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).border(1.dp, if (item.answeredBy == null) Pool.colors.accent else Pool.colors.line, RoundedCornerShape(10.dp)).padding(12.dp)) {
            for (q in item.questions) Text(q.question, style = MaterialTheme.typography.titleSmall)
            val expired = item.expiresAt != null && item.expiresAt!! <= now
            Text(
                when {
                    item.answeredBy == "phone" -> "Answered from a phone"
                    item.answeredBy != null -> "Answered at the computer"
                    expired -> "No longer answerable here: it is asked at the computer"
                    else -> "Waiting for your answer"
                },
                style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(top = 4.dp),
            )
        }
        is Item.Notice -> Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(Pool.colors.sunken).padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(if (item.kind == "permission" || item.kind == "dropped") Icons.Alert else Icons.Bell, null,
                tint = if (item.kind == "permission" || item.kind == "dropped") Pool.colors.warn else Pool.colors.muted, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(8.dp))
            Text(item.text, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        }
    }
}

@Composable
private fun TextAction(text: String, onClick: () -> Unit) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = Pool.colors.accentInk, modifier = Modifier.clickable(onClick = onClick).padding(horizontal = 10.dp, vertical = 8.dp))
}

/** What was said to the agent: a bubble on the right. */
@Composable
private fun Mine(text: String, label: String, dim: Boolean = false) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
        if (label.isNotEmpty()) Text(label, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(bottom = 3.dp))
        val shape = RoundedCornerShape(topStart = 14.dp, topEnd = 14.dp, bottomStart = 14.dp, bottomEnd = 4.dp)
        SelectionContainer {
            Text(
                text,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.widthIn(max = 320.dp).clip(shape).background(if (dim) Pool.colors.sunken else Pool.colors.accentDim).padding(horizontal = 13.dp, vertical = 9.dp),
            )
        }
    }
}

/** The tools an agent ran, folded to one line until opened. */
@Composable
private fun Steps(item: Item.Steps) {
    var open by rememberSaveable(item.key) { mutableStateOf(false) }
    val failed = item.steps.count { !it.ok }
    val shape = RoundedCornerShape(10.dp)
    Column(Modifier.fillMaxWidth().clip(shape).border(1.dp, Pool.colors.line, shape).animateContentSize()) {
        Row(Modifier.fillMaxWidth().clickable { open = !open }.heightIn(min = 40.dp).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Tool, null, tint = if (failed > 0) Pool.colors.bad else Pool.colors.muted, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(8.dp))
            val head = if (item.steps.size == 1) item.steps[0].text else "${item.steps.size} steps · ${item.steps.last().text}"
            Text(head, style = Pool.mono, color = Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (failed > 0) Tag(if (item.steps.size == 1) "failed" else "$failed failed", Pool.colors.bad)
            Spacer(Modifier.width(4.dp))
            Icon(if (open) Icons.Down else Icons.Forward, if (open) "Hide steps" else "Show steps", tint = Pool.colors.muted, modifier = Modifier.size(16.dp))
        }
        if (open) {
            Column(Modifier.padding(start = 8.dp, end = 8.dp, bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                for (step in item.steps) {
                    if (step.ok) CodeBlock(step.text) else Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Alert, "Failed", tint = Pool.colors.bad, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(6.dp))
                        CodeBlock(step.text)
                    }
                }
            }
        }
    }
}

/** The answer dock above the send box: the questions of one ask, answered together. */
@Composable
private fun QuestionDock(ask: Item.Ask, now: Double, waiting: Int, busy: Boolean, onAnswer: (Map<String, String>) -> Unit) {
    val picked = remember(ask.question) { mutableStateMapOf<String, Set<String>>() }
    val own = remember(ask.question) { mutableStateMapOf<String, String>() }
    fun answer(question: String): String {
        val typed = own[question]?.trim().orEmpty()
        val chosen = picked[question].orEmpty()
        return (chosen.toList() + listOfNotNull(typed.ifEmpty { null })).joinToString(", ")
    }
    val ready = ask.questions.isNotEmpty() && ask.questions.all { answer(it.question).isNotEmpty() }
    Column(Modifier.fillMaxWidth().background(Pool.colors.surface).heightIn(max = 420.dp).padding(horizontal = 14.dp, vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Tag("Needs your answer", Pool.colors.accent, filled = true)
            Spacer(Modifier.weight(1f))
            val left = until(ask.expiresAt, now)
            Text(listOf(if (waiting > 1) "$waiting waiting" else "", if (left.isEmpty()) "" else "ends $left").filter { it.isNotEmpty() }.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
        }
        Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState())) {
            for (q in ask.questions) {
                if (q.header.isNotBlank()) Text(q.header.uppercase(), style = MaterialTheme.typography.labelSmall, color = Pool.colors.muted, modifier = Modifier.padding(top = 8.dp))
                Text(q.question, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 4.dp, bottom = 4.dp))
                for (option in q.options) {
                    val chosen = option.label in picked[q.question].orEmpty()
                    val pick = {
                        val held = picked[q.question].orEmpty()
                        picked[q.question] = if (!q.multiSelect) setOf(option.label) else if (chosen) held - option.label else held + option.label
                        if (!q.multiSelect) own.remove(q.question)
                        Unit
                    }
                    Row(Modifier.fillMaxWidth().clickable(onClick = pick).heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (q.multiSelect) Checkbox(chosen, { pick() }, colors = CheckboxDefaults.colors(checkedColor = Pool.colors.accent, checkmarkColor = Pool.colors.onAccent))
                        else RadioButton(chosen, pick, colors = RadioButtonDefaults.colors(selectedColor = Pool.colors.accent))
                        Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
                            Text(option.label, style = MaterialTheme.typography.bodyLarge)
                            if (option.description.isNotBlank()) Text(option.description, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
                        }
                    }
                }
                OutlinedTextField(
                    own[q.question].orEmpty(),
                    {
                        own[q.question] = it
                        if (!q.multiSelect && it.isNotBlank()) picked.remove(q.question)
                    },
                    placeholder = { Text(if (q.options.isEmpty()) "Your answer" else "Or your own words") },
                    modifier = Modifier.fillMaxWidth(),
                    maxLines = 4,
                )
            }
        }
        PrimaryButton("Send answer", { onAnswer(ask.questions.associate { it.question to answer(it.question) }) },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp), enabled = ready, working = busy)
    }
}

/** The send box, with how the message will reach this agent above it; a session that cannot receive one says why instead. */
@Composable
private fun Composer(session: Session, onSend: (String) -> Unit) {
    var text by rememberSaveable(session.id) { mutableStateOf("") }
    Column(Modifier.fillMaxWidth().background(Pool.colors.surface).padding(horizontal = 12.dp, vertical = 8.dp)) {
        val how = session.note.ifBlank { deliveryLabel(session.mode) }
        if (how.isNotEmpty()) {
            Row(Modifier.padding(start = 2.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(if (session.canSend) Icons.Clock else Icons.Alert, null, tint = if (session.canSend) Pool.colors.muted else Pool.colors.warn, modifier = Modifier.size(14.dp))
                Spacer(Modifier.width(6.dp))
                Text(how, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
            }
        }
        if (!session.canSend) return@Column
        Row(verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(
                text,
                { if (it.length <= MESSAGE_LIMIT) text = it },
                placeholder = { Text("Message ${session.agent.ifBlank { kindLabel(session.kind) }}") },
                modifier = Modifier.weight(1f),
                maxLines = 6,
                shape = RoundedCornerShape(14.dp),
                colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Pool.colors.line, focusedBorderColor = Pool.colors.accent),
            )
            Spacer(Modifier.width(8.dp))
            val ready = text.isNotBlank()
            Box(
                Modifier.padding(bottom = 4.dp).size(48.dp).clip(CircleShape).background(if (ready) Pool.colors.accent else Pool.colors.sunken)
                    .clickable(enabled = ready) {
                        onSend(text)
                        text = ""
                    }
                    .semantics { contentDescription = "Send" },
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.Send, null, tint = if (ready) Pool.colors.onAccent else Pool.colors.muted) }
        }
    }
}
