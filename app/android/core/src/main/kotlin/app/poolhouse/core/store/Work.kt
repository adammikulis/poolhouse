package app.poolhouse.core.store

import app.poolhouse.core.api.Job
import app.poolhouse.core.api.Run
import app.poolhouse.core.api.Scheduled
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull

enum class WorkKind(val label: String) { DELEGATE("Delegates"), JOB("Jobs"), SCHEDULED("Scheduled") }

/** How a piece of work stands, for its colour and its group. */
enum class Phase { LIVE, WAITING, GOOD, BAD, STOPPED }

private val LIVE_RUN = setOf("starting", "loading", "working", "asking")
private val LIVE_JOB = setOf("queued", "preparing", "running")

fun phaseOf(state: String): Phase = when (state) {
    "queued", "waiting", "asking", "partial" -> Phase.WAITING
    "starting", "loading", "working", "preparing", "running", "installing" -> Phase.LIVE
    "done", "passed" -> Phase.GOOD
    "failed", "rejected", "unapplied", "error" -> Phase.BAD
    else -> Phase.STOPPED
}

data class WorkRow(
    val kind: WorkKind,
    val id: String,
    val title: String,
    val state: String,
    val phase: Phase,
    val detail: String,
    /** When it started, or for a scheduled job when it runs next. */
    val at: Double?,
    val live: Boolean,
    val cancellable: Boolean,
)

fun delegateRow(run: Run): WorkRow = WorkRow(
    kind = WorkKind.DELEGATE,
    id = run.name,
    title = run.task.lineSequence().firstOrNull { it.isNotBlank() }?.trim() ?: run.name,
    state = run.state,
    phase = phaseOf(run.state),
    detail = listOf(run.name, run.doing, run.model).filter { it.isNotBlank() }.joinToString(" · "),
    at = run.started,
    live = run.state in LIVE_RUN,
    cancellable = run.state in LIVE_RUN,
)

fun jobRow(job: Job): WorkRow = WorkRow(
    kind = WorkKind.JOB,
    id = job.id,
    title = job.name.ifBlank { job.id },
    state = job.state,
    phase = phaseOf(job.state),
    detail = job.id,
    at = job.startedAt ?: job.submittedAt,
    live = job.state in LIVE_JOB,
    cancellable = job.state in LIVE_JOB,
)

fun scheduledRow(job: Scheduled): WorkRow = WorkRow(
    kind = WorkKind.SCHEDULED,
    id = job.id,
    title = job.name.ifBlank { job.id },
    state = if (job.enabled) "scheduled" else "off",
    phase = if (job.enabled) Phase.WAITING else Phase.STOPPED,
    detail = listOf(job.schedule, job.lastState.let { if (it.isBlank()) "" else "last: $it" }).filter { it.isNotBlank() }.joinToString(" · "),
    at = job.nextAt,
    live = false,
    cancellable = job.enabled,
)

/** One line of a delegate's record (poolhouse.delegate_record), as the phone shows it. */
data class RecordLine(val at: Double, val title: String, val body: String, val phase: Phase?)

private fun JsonObject.text(key: String): String = (this[key] as? JsonPrimitive)?.contentOrNull ?: ""

private fun JsonObject.number(key: String): Long? = (this[key] as? JsonPrimitive)?.doubleOrNull?.toLong()

private fun JsonObject.flag(key: String): Boolean = (this[key] as? JsonPrimitive)?.booleanOrNull == true

/** The lines shown of a record's events; an event of a kind not listed here is left out rather than guessed at. */
fun recordLines(events: List<JsonObject>): List<RecordLine> = events.mapNotNull { e ->
    val at = (e["at"] as? JsonPrimitive)?.doubleOrNull ?: 0.0
    val about = e.text("command").ifEmpty { e.text("file") }.ifEmpty { e.text("about") }
    when (e.text("kind")) {
        "state" -> RecordLine(at, "State: ${e.text("state")}", e.text("detail"), phaseOf(e.text("state")))
        "attempt" -> RecordLine(at, "Attempt ${e.number("attempt") ?: 0}, round ${e.number("round") ?: 0}", e.text("phase"), null)
        "call" -> RecordLine(at, (if (e.flag("denied")) "Denied: " else "") + e.text("tool"), about, if (e.flag("denied")) Phase.BAD else null)
        "result" -> {
            val exit = e.number("exit")
            val failed = e.text("status") == "error" || (exit != null && exit != 0L)
            RecordLine(at, "${e.text("tool")} ${if (failed) "failed" else "finished"}${exit?.let { " (exit $it)" } ?: ""}",
                e.text("output").ifEmpty { about }, if (failed) Phase.BAD else Phase.GOOD)
        }
        "text" -> RecordLine(at, "Agent", e.text("text"), null)
        "test" -> {
            val counts = listOf("passed", "failed", "errors", "skipped").mapNotNull { k -> e.number(k)?.let { "$it $k" } }.joinToString(", ")
            val ok = e.flag("ok")
            RecordLine(at, "Tests: ${counts.ifEmpty { if (ok) "ended well" else "failed" }}", e.text("command"), if (ok) Phase.GOOD else Phase.BAD)
        }
        "error" -> RecordLine(at, "Error", e.text("why"), Phase.BAD)
        "notice" -> RecordLine(at, "Notice", e.text("text").ifEmpty { e.text("detail") }, Phase.WAITING)
        else -> null
    }
}
