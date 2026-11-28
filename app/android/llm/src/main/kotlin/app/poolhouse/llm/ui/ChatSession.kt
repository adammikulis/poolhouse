/** The conversation with the model on the phone: held in memory only, answered with no computer. */
package app.poolhouse.llm.ui

import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import app.poolhouse.llm.android.LocalModels
import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.Caller
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.ChatTurn
import app.poolhouse.llm.engine.Finish
import app.poolhouse.llm.engine.GenerationOptions
import app.poolhouse.llm.engine.GenerationStats
import app.poolhouse.llm.engine.Role
import app.poolhouse.llm.model.CatalogModel
import kotlin.concurrent.thread

/** One message on the screen; `note` is what it cost, or why it ended. */
data class Line(val turn: ChatTurn, val note: String? = null)

object ChatSession {
    val lines = mutableStateListOf<Line>()
    var writing by mutableStateOf<String?>(null)
        private set
    var status by mutableStateOf("")
        private set
    var accelerator by mutableStateOf<Accelerator?>(null)
        private set
    var last by mutableStateOf<GenerationStats?>(null)
        private set

    /** What is typed and not yet sent. */
    var draft by mutableStateOf("")

    private var running: CancelToken? = null
    private val screen = Handler(Looper.getMainLooper())

    val busy: Boolean get() = writing != null

    fun stop() {
        running?.cancel("Stopped.")
    }

    fun clear() {
        stop()
        lines.clear()
        last = null
        status = ""
    }

    fun send(models: LocalModels, model: CatalogModel, text: String, pictures: List<ByteArray>) {
        if (busy || (text.isBlank() && pictures.isEmpty())) return
        lines += Line(ChatTurn(Role.USER, text, pictures))
        val turns = lines.map { it.turn }
        val cancel = CancelToken()
        running = cancel
        writing = ""
        status = "Loading ${model.title}…"
        thread(name = "local-chat") {
            val reply = StringBuilder()
            val outcome = runCatching {
                models.generate(
                    Caller.PERSON, model, turns, GenerationOptions(), cancel,
                    onLoaded = { screen.post { if (running === cancel) { accelerator = it; status = "Answering…" } } },
                ) { piece ->
                    val soFar = reply.append(piece).toString()
                    screen.post { if (running === cancel) writing = soFar }
                }
            }
            screen.post {
                if (running !== cancel) return@post
                running = null
                writing = null
                status = ""
                // A cleared conversation takes nothing of an old reply.
                if (lines.isEmpty()) return@post
                val result = outcome.getOrNull()
                last = result?.stats ?: last
                val note = when {
                    result == null -> outcome.exceptionOrNull()?.message ?: "The model could not answer."
                    result.finish == Finish.CANCELLED -> "Stopped."
                    else -> cost(result.stats) + if (result.finish == Finish.LENGTH) " · cut at its length limit" else ""
                }
                if (reply.isNotEmpty()) {
                    lines += Line(ChatTurn(Role.ASSISTANT, reply.toString()), note)
                } else {
                    // A question with no answer goes back to the box, so the conversation stays in turns.
                    draft = lines.removeAt(lines.lastIndex).turn.text
                    status = note
                }
            }
        }
    }

    fun cost(stats: GenerationStats): String = listOfNotNull(
        stats.timeToFirstTokenMs?.let { "first word %.1f s".format(it / 1000.0) },
        stats.outputTokensPerSecond?.let { "%.0f tokens/s".format(it) },
        stats.outputTokens?.let { "$it tokens" },
    ).joinToString(" · ")
}
