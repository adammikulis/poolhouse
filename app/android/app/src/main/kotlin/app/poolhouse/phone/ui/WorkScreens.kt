package app.poolhouse.phone.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import app.poolhouse.core.store.Phase
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.RecordLine
import app.poolhouse.core.store.WorkKind
import app.poolhouse.core.store.WorkRow
import app.poolhouse.core.store.ago
import app.poolhouse.core.store.delegateRow
import app.poolhouse.core.store.facts
import app.poolhouse.core.store.jobRow
import app.poolhouse.core.store.recordLines
import app.poolhouse.core.store.scheduledRow
import app.poolhouse.core.store.until
import app.poolhouse.core.theme.Section
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.PoolSession
import app.poolhouse.phone.Screen
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.Chips
import app.poolhouse.phone.ui.components.CodeBlock
import app.poolhouse.phone.ui.components.Dot
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.components.Fact
import app.poolhouse.phone.ui.components.Markdown
import app.poolhouse.phone.ui.components.SecondaryButton
import app.poolhouse.phone.ui.components.SectionLabel
import app.poolhouse.phone.ui.components.Tag
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.phone.ui.components.phaseColor
import app.poolhouse.phone.ui.theme.Pool
import app.poolhouse.phone.ui.theme.tint
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonObject

private enum class WorkFilter(val label: String, val kind: WorkKind?) {
    ALL("All", null), DELEGATES("Delegates", WorkKind.DELEGATE), JOBS("Jobs", WorkKind.JOB), SCHEDULED("Scheduled", WorkKind.SCHEDULED)
}

private fun rows(state: PoolState): List<WorkRow> =
    state.runs.value?.runs.orEmpty().map(::delegateRow) +
        state.jobs.value?.jobs.orEmpty().map(::jobRow) +
        state.scheduled.value?.jobs.orEmpty().mapNotNull(::scheduledRow)

@Composable
fun WorkScreen(model: PhoneViewModel, state: PoolState) {
    var filter by rememberSaveable { mutableStateOf(WorkFilter.ALL) }
    val all = remember(state.runs, state.jobs, state.scheduled) { rows(state) }
    val shown = all.filter { filter.kind == null || it.kind == filter.kind }
    val live = shown.filter { it.live }
    val rest = shown.filterNot { it.live }.sortedByDescending { it.at ?: 0.0 }
    val now = rememberNow()
    Column(Modifier.fillMaxSize()) {
        TopBar("Work", subtitle = connectionLine(state))
        Chips(WorkFilter.entries, filter, { it.label }, Section.WORK.tint(), { filter = it })
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 88.dp)) {
            val errors = listOf("Delegates" to state.runs.error, "Jobs" to state.jobs.error, "Scheduled jobs" to state.scheduled.error).filter { it.second.isNotEmpty() }
            items(errors, key = { "error:${it.first}" }) { (what, why) ->
                Text("$what: $why", style = MaterialTheme.typography.bodySmall, color = Pool.colors.bad, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
            }
            if (live.isNotEmpty()) item { SectionLabel("Running") }
            items(live, key = { "${it.kind}:${it.id}" }) { WorkLine(it, now) { model.open(Screen.Work(it.kind, it.id)) } }
            if (rest.isNotEmpty()) item { SectionLabel(if (filter == WorkFilter.SCHEDULED) "Scheduled" else "Earlier") }
            items(rest, key = { "${it.kind}:${it.id}" }) { WorkLine(it, now) { model.open(Screen.Work(it.kind, it.id)) } }
            if (shown.isEmpty()) {
                val reading = state.runs.value == null && state.jobs.value == null
                val noSchedule = filter == WorkFilter.SCHEDULED && state.scheduled.value?.available == false
                item {
                    when {
                        noSchedule -> EmptyState("No scheduled jobs yet", "This computer does not have scheduled jobs.")
                        reading -> EmptyState("Reading work", "Waiting for ${state.computer.ifEmpty { "the computer" }}.")
                        else -> EmptyState("No work", "Delegates, jobs and scheduled jobs appear here.")
                    }
                }
            }
        }
    }
}

@Composable
private fun WorkLine(row: WorkRow, now: Double, onOpen: () -> Unit) {
    Column {
        Row(Modifier.fillMaxWidth().clickable(onClick = onOpen).heightIn(min = 60.dp).padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Dot(phaseColor(row.phase), hollow = row.phase == Phase.STOPPED)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(row.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (row.detail.isNotBlank()) Text(row.detail, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.width(8.dp))
            Column(horizontalAlignment = Alignment.End) {
                Tag(row.state, phaseColor(row.phase))
                val time = if (row.kind == WorkKind.SCHEDULED) until(row.at, now) else ago(row.at, now)
                if (time.isNotEmpty()) Text(time, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
            }
        }
        HorizontalDivider(color = Pool.colors.line, modifier = Modifier.padding(start = 37.dp))
    }
}

@Composable
fun WorkDetailScreen(model: PhoneViewModel, pool: PoolSession, state: PoolState, kind: WorkKind, id: String) {
    val row = rows(state).firstOrNull { it.kind == kind && it.id == id }
    var confirming by remember { mutableStateOf(false) }
    val now = rememberNow(5_000)
    // The log is followed while this screen is on top and the app is on screen.
    var events by remember(id) { mutableStateOf<List<JsonObject>>(emptyList()) }
    var answer by remember(id) { mutableStateOf("") }
    var task by remember(id) { mutableStateOf("") }
    var log by remember(id) { mutableStateOf<String?>(null) }
    var extra by remember(id) { mutableStateOf(emptyList<Pair<String, String>>()) }
    var error by remember(id) { mutableStateOf("") }
    val live = row?.live == true
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(kind, id, live) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            var next = 0L
            events = emptyList()
            do {
                try {
                    when (kind) {
                        WorkKind.DELEGATE -> {
                            do {
                                val page = pool.api.delegate(id, next)
                                events = (events + page.events).takeLast(600)
                                next = page.next
                                answer = page.run.answer
                                task = page.run.task
                            } while (page.more)
                        }
                        WorkKind.JOB -> log = pool.api.jobLog(id).log
                        WorkKind.SCHEDULED -> {
                            val detail = pool.api.scheduledJob(id)
                            extra = facts(detail.job)
                            log = detail.log
                        }
                    }
                    error = ""
                } catch (cancelled: kotlinx.coroutines.CancellationException) {
                    throw cancelled
                } catch (failed: Exception) {
                    error = failed.message ?: "Not read."
                }
                if (live) delay(2_000)
            } while (live)
        }
    }
    val lines = remember(events) { recordLines(events) }
    val list = rememberLazyListState()
    LaunchedEffect(lines.size, log?.length) {
        // Follow the newest line while the person is already at the end.
        val last = list.layoutInfo.totalItemsCount - 1
        val seen = list.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
        if (live && last >= 0 && seen >= last - 2) list.scrollToItem(last)
    }
    Column(Modifier.fillMaxSize()) {
        TopBar(row?.title ?: id, subtitle = row?.let { "${it.kind.label.removeSuffix("s")} · ${it.state}" } ?: "", onBack = model::back)
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            item {
                Card {
                    Fact("Name", id, mono = true)
                    Fact("State", row?.state ?: "No longer listed")
                    Fact("Detail", row?.detail.orEmpty())
                    val time = if (kind == WorkKind.SCHEDULED) until(row?.at, now) else ago(row?.at, now).let { if (it.isEmpty() || it == "now") it else "$it ago" }
                    Fact(if (kind == WorkKind.SCHEDULED) "Next run" else "Started", time)
                    Fact("Task", task)
                    for ((name, value) in extra) Fact(name, value)
                }
            }
            if (error.isNotEmpty()) item { Text(error, style = MaterialTheme.typography.bodySmall, color = Pool.colors.bad) }
            items(lines) { RecordRow(it) }
            log?.let { text ->
                item { Text("LOG, NEWEST LINES", style = MaterialTheme.typography.labelSmall, color = Pool.colors.muted) }
                item { CodeBlock(text.ifBlank { "Nothing written yet." }) }
            }
            if (answer.isNotBlank()) {
                item { Text("ANSWER", style = MaterialTheme.typography.labelSmall, color = Pool.colors.muted) }
                item { Markdown(answer) }
            }
        }
        if (row != null && (row.cancellable || kind == WorkKind.DELEGATE)) {
            HorizontalDivider(color = Pool.colors.line)
            Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (kind == WorkKind.DELEGATE) {
                    SecondaryButton("Conversation", { model.open(Screen.Conversation("delegate-$id")) }, Modifier.weight(1f),
                        enabled = state.sessions.value?.sessions?.any { it.id == "delegate-$id" } == true)
                }
                if (row.cancellable) {
                    SecondaryButton(if (kind == WorkKind.SCHEDULED) "Cancel schedule" else "Cancel", { confirming = true }, Modifier.weight(1f), danger = true, working = "stop:$id" in state.busy)
                }
            }
        }
    }
    if (confirming && row != null) {
        AlertDialog(
            onDismissRequest = { confirming = false },
            title = { Text("Cancel “${row.title}”?") },
            text = { Text(if (kind == WorkKind.SCHEDULED) "It will not run again." else "The work is stopped on the computer.") },
            confirmButton = {
                TextButton({
                    confirming = false
                    when (kind) {
                        WorkKind.DELEGATE -> pool.store.stopDelegate(id)
                        WorkKind.JOB -> pool.store.stopJob(id)
                        WorkKind.SCHEDULED -> pool.store.cancelScheduled(id)
                    }
                }) { Text("Cancel it", color = Pool.colors.bad) }
            },
            dismissButton = { TextButton({ confirming = false }) { Text("Keep it") } },
        )
    }
}

@Composable
private fun RecordRow(line: RecordLine) {
    Column(Modifier.fillMaxWidth()) {
        Text(line.title, style = MaterialTheme.typography.labelLarge, color = line.phase?.let { phaseColor(it) } ?: Pool.colors.text)
        if (line.body.isNotBlank()) {
            if (line.title == "Agent") Markdown(line.body) else CodeBlock(line.body)
        }
    }
}
