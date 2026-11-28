/**
 * Measurements of a runtime on the phone, run from a computer:
 *
 *     adb shell am instrument -w -r -e action run -e model gemma-4-e2b-litert -e context 32000 -e sentences 100 \
 *         <application id>/app.poolhouse.llm.bench.BenchRunner
 *
 * Every line of the result starts `BENCH` and is one JSON object.
 */
package app.poolhouse.llm.bench

import android.app.Activity
import android.app.ActivityManager
import android.app.Instrumentation
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Debug
import android.os.PowerManager
import android.os.Process
import android.util.Log
import app.poolhouse.llm.android.LiteRtEngine
import app.poolhouse.llm.android.batteryCelsius
import app.poolhouse.llm.android.metered
import app.poolhouse.llm.android.phoneCondition
import app.poolhouse.llm.android.phoneRoom
import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.ChatTurn
import app.poolhouse.llm.engine.GenerationOptions
import app.poolhouse.llm.engine.LoadSettings
import app.poolhouse.llm.engine.LoadedModel
import app.poolhouse.llm.engine.Role
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.model.CatalogModel
import app.poolhouse.llm.model.Downloader
import app.poolhouse.llm.model.Licence
import app.poolhouse.llm.model.ModelFile
import app.poolhouse.llm.model.ModelStore
import java.io.File
import kotlin.concurrent.thread
import org.json.JSONObject

private const val TAG = "PoolhouseBench"

class BenchRunner : Instrumentation() {
    private lateinit var arguments: Bundle

    override fun onCreate(arguments: Bundle) {
        super.onCreate(arguments)
        this.arguments = arguments
        start()
    }

    override fun onStart() {
        val context = targetContext
        val wake = context.getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "poolhouse:bench")
        @Suppress("DEPRECATION")
        val wifi = context.getSystemService(WifiManager::class.java)?.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "poolhouse:bench")
        wake.acquire()
        runCatching { wifi?.acquire() }
        try {
            say(JSONObject().put("event", "start").put("pid", Process.myPid()).put("action", text("action", "info")))
            when (text("action", "info")) {
                "info" -> info()
                "download" -> download()
                "run", "quality", "speed", "sustain" -> measure()
                "pool" -> poolBench(context, text("model"), number("context", 8192), ::say)
                "delete" -> say(JSONObject().put("event", "deleted").put("ok", store().delete(model())))
                else -> throw IllegalArgumentException("No such action.")
            }
        } catch (failure: Throwable) {
            Log.e(TAG, "failed", failure)
            val chain = generateSequence(failure) { it.cause }.joinToString(" <- ") { "${it.javaClass.simpleName}: ${it.message}" }
            say(JSONObject().put("event", "error").put("error", chain))
        } finally {
            runCatching { wifi?.release() }
            wake.release()
            finish(Activity.RESULT_OK, Bundle())
        }
    }

    private fun text(key: String, otherwise: String? = null): String =
        arguments.getString(key) ?: otherwise ?: throw IllegalArgumentException("Give -e $key.")

    private fun number(key: String, otherwise: Int): Int = arguments.getString(key)?.toInt() ?: otherwise

    private fun say(line: JSONObject) {
        Log.i(TAG, "BENCH $line")
        sendStatus(1, Bundle().apply { putString(REPORT_KEY_STREAMRESULT, "BENCH $line\n") })
    }

    private fun store() = ModelStore(File(targetContext.filesDir, "llm-models"))

    /** A catalogue model by id, or one file named by `repository`, `revision`, `file`, `bytes` and `sha256`. */
    private fun model(): CatalogModel {
        arguments.getString("model")?.let { return Catalog.byId(it) ?: throw IllegalArgumentException("No model $it.") }
        val file = text("file")
        return CatalogModel(
            id = "bench-" + file.substringBeforeLast('.'),
            title = file,
            repository = text("repository"),
            revision = text("revision"),
            file = ModelFile(file, text("bytes").toLong(), text("sha256")),
            licence = Licence("see repository", ""),
            images = false,
            contextLimit = 131_072,
            about = "A file under measurement.",
        )
    }

    private fun info() {
        val room = phoneRoom(targetContext, store().freeSpace())
        val condition = phoneCondition(targetContext)
        say(
            JSONObject().put("event", "phone").put("model", Build.MODEL).put("soc", Build.SOC_MODEL).put("api", Build.VERSION.SDK_INT)
                .put("totalMemory", room.totalMemory).put("availableMemory", room.availableMemory)
                .put("lowThreshold", room.lowMemoryThreshold).put("freeStorage", room.freeStorage)
                .put("metered", metered(targetContext)).put("charging", condition.charging).put("battery", condition.batteryPercent)
                .put("thermal", condition.thermalStatus).put("batteryC", batteryCelsius(targetContext))
                .put("powerSave", condition.powerSave)
                .put("interactive", targetContext.getSystemService(PowerManager::class.java).isInteractive),
        )
        val manager = targetContext.getSystemService(ActivityManager::class.java)
        manager.getHistoricalProcessExitReasons(null, 0, number("exits", 5)).forEach {
            say(
                JSONObject().put("event", "exit").put("pid", it.pid).put("reason", it.reason).put("status", it.status)
                    .put("importance", it.importance).put("pssKb", it.pss).put("rssKb", it.rss)
                    .put("description", it.description).put("at", it.timestamp),
            )
        }
    }

    private fun download() {
        val model = model()
        check(!metered(targetContext)) { "The network is metered; measurements download on Wi-Fi only." }
        var told = 0L
        val started = System.nanoTime()
        Downloader(store()).fetch(model, CancelToken()) { held ->
            if (held - told >= 250_000_000 || held == model.bytes) {
                told = held
                say(JSONObject().put("event", "progress").put("held", held).put("of", model.bytes))
            }
        }
        say(
            JSONObject().put("event", "downloaded").put("model", model.id).put("bytes", model.bytes)
                .put("seconds", (System.nanoTime() - started) / 1e9),
        )
    }

    private fun measure() {
        val model = model()
        val weights = store().file(model) ?: throw IllegalStateException("The model is not downloaded.")
        val accelerator = if (text("accelerator", "gpu") == "cpu") Accelerator.CPU else Accelerator.GPU
        val context = number("context", 4096)
        val pictures = text("image", "no") == "yes"
        val before = phoneRoom(targetContext, 0).availableMemory
        val watch = MemoryWatch()
        say(
            JSONObject().put("event", "loading").put("model", model.id).put("accelerator", accelerator.label).put("context", context)
                .put("availableBefore", before).put("thermal", phoneCondition(targetContext).thermalStatus)
                .put("powerSave", phoneCondition(targetContext).powerSave).put("battery", phoneCondition(targetContext).batteryPercent)
                .put("charging", phoneCondition(targetContext).charging)
                .put("batteryC", batteryCelsius(targetContext)).put("cpuset", File("/proc/self/cpuset").readText().trim()),
        )
        val temperature = text("temperature", "0").toDoubleOrNull()
        val precise = text("activation", "default") == "fp32"
        val drafting = text("drafting", "no") == "yes"
        val action = text("action", "run")
        try {
            val loading = System.nanoTime()
            val images = pictures || (action == "speed" && model.images)
            val settings = LoadSettings(accelerator, context, images, File(targetContext.cacheDir, "llm"), precise, drafting)
            LiteRtEngine().load(weights, settings).use { loaded ->
                say(
                    JSONObject().put("event", "loaded").put("accelerator", loaded.accelerator.label).put("fp32", precise)
                        .put("drafting", drafting).put("images", loaded.acceptsImages)
                        .put("temperature", temperature ?: "model").put("seconds", (System.nanoTime() - loading) / 1e9)
                        .put("memory", watch.report()),
                )
                if (action == "quality") return@use quality(loaded, temperature)
                if (action == "speed") return@use speed(loaded, context, temperature, watch)
                if (action == "sustain") return@use sustain(loaded, temperature, number("seconds", 300))
                val sentences = number("sentences", 0)
                val rounds = number("rounds", 1)
                for (round in 1..rounds) {
                    val secret = 1000 + (System.nanoTime() % 9000).toInt()
                    val needle = if (sentences > 0) needle(sentences, number("seed", 7) + round) else null
                    val turn = when {
                        pictures -> ChatTurn(Role.USER, "What number is written in this picture? Reply with the number only.", listOf(numberPicture(secret)))
                        needle != null -> ChatTurn(Role.USER, needle.text)
                        else -> ChatTurn(Role.USER, text("prompt", "Explain in about 200 words why the sky is blue."))
                    }
                    val reply = StringBuilder()
                    var pieces = 0
                    val result = loaded.generate(listOf(turn), GenerationOptions(maxTokens = number("output", 256), temperature = temperature), CancelToken()) {
                        reply.append(it)
                        pieces++
                    }
                    val stats = result.stats
                    val line = JSONObject().put("event", "reply").put("round", round).put("finish", result.finish.wire)
                        .put("promptChars", turn.text.length).put("promptTokens", stats.promptTokens).put("outputTokens", stats.outputTokens)
                        .put("ttftMs", stats.timeToFirstTokenMs).put("prefillPerSecond", stats.promptTokensPerSecond)
                        .put("decodePerSecond", stats.outputTokensPerSecond).put("contextUsed", stats.contextUsed)
                        .put("pieces", pieces).put("text", reply.toString().take(600))
                        .put("memory", watch.report()).put("thermal", phoneCondition(targetContext).thermalStatus)
                        .put("powerSave", phoneCondition(targetContext).powerSave).put("batteryC", batteryCelsius(targetContext))
                    if (needle != null) {
                        line.put("first", needle.first).put("last", needle.last)
                            .put("foundFirst", reply.contains(needle.first, ignoreCase = true))
                            .put("foundLast", reply.contains(needle.last, ignoreCase = true))
                    }
                    if (pictures) line.put("number", secret).put("foundNumber", reply.contains(secret.toString()))
                    say(line)
                }
            }
        } finally {
            watch.stop()
            say(JSONObject().put("event", "memory").put("memory", watch.report()))
        }
    }

    /** One loaded model: a short question, a picture, a prompt near the context's size, and a follow-up to it. */
    private fun speed(loaded: LoadedModel, context: Int, temperature: Double?, watch: MemoryWatch) {
        fun step(name: String, turns: List<ChatTurn>, output: Int, found: (String) -> Boolean): String {
            val reply = StringBuilder()
            val result = loaded.generate(turns, GenerationOptions(maxTokens = output, temperature = temperature), CancelToken()) {
                reply.append(it)
            }
            val stats = result.stats
            say(
                JSONObject().put("event", "step").put("step", name).put("promptTokens", stats.promptTokens)
                    .put("outputTokens", stats.outputTokens).put("ttftMs", stats.timeToFirstTokenMs)
                    .put("prefillPerSecond", stats.promptTokensPerSecond).put("decodePerSecond", stats.outputTokensPerSecond)
                    .put("contextUsed", stats.contextUsed).put("right", found(reply.toString())).put("text", reply.toString().take(120))
                    .put("memory", watch.report()).put("thermal", phoneCondition(targetContext).thermalStatus)
                    .put("powerSave", phoneCondition(targetContext).powerSave).put("batteryC", batteryCelsius(targetContext)),
            )
            return reply.toString()
        }
        step("short", listOf(ChatTurn(Role.USER, "Explain in about 150 words why the sky is blue.")), 220) { true }
        if (loaded.acceptsImages) {
            val secret = 1000 + (System.nanoTime() % 9000).toInt()
            val picture = ChatTurn(Role.USER, "What number is written in this picture? Reply with the number only.", listOf(numberPicture(secret)))
            step("picture", listOf(picture), 16) { it.contains(secret.toString()) }
        }
        // About 32 tokens a ledger line; room is left for the question and the answers.
        val needle = needle((context - 900) / 32, number("seed", 7))
        val asked = ChatTurn(Role.USER, needle.text)
        val answer = step("near-full", listOf(asked), 40) { it.contains(needle.first) && it.contains(needle.last) }
        val again = ChatTurn(Role.USER, "Now reply with the SOUTH archive's access word only.")
        step("follow-up", listOf(asked, ChatTurn(Role.ASSISTANT, answer), again), 16) { it.contains(needle.last) }
    }

    /** Long replies one after another for `seconds`, to see the speed the phone keeps as it warms. */
    private fun sustain(loaded: LoadedModel, temperature: Double?, seconds: Int) {
        val started = System.nanoTime()
        var round = 0
        while ((System.nanoTime() - started) / 1e9 < seconds) {
            round++
            val result = loaded.generate(
                listOf(ChatTurn(Role.USER, "Write a long and detailed story, number $round, about a lighthouse keeper.")),
                GenerationOptions(maxTokens = 600, temperature = temperature, keep = false),
                CancelToken(),
            ) {}
            say(
                JSONObject().put("event", "sustain").put("round", round).put("at", (System.nanoTime() - started) / 1e9)
                    .put("outputTokens", result.stats.outputTokens).put("decodePerSecond", result.stats.outputTokensPerSecond)
                    .put("ttftMs", result.stats.timeToFirstTokenMs).put("thermal", phoneCondition(targetContext).thermalStatus)
                    .put("powerSave", phoneCondition(targetContext).powerSave).put("batteryC", batteryCelsius(targetContext)),
            )
        }
    }

    /** Every fixed task, each as a new conversation, with the reply and whether the program's check passed. */
    private fun quality(loaded: LoadedModel, temperature: Double?) {
        var passed = 0
        var copied = 0
        for (task in COPY_TASKS + QUALITY_TASKS) {
            val reply = StringBuilder()
            val result = loaded.generate(
                listOf(ChatTurn(Role.USER, task.prompt)),
                GenerationOptions(maxTokens = number("output", 320), temperature = temperature, keep = false),
                CancelToken(),
            ) { reply.append(it) }
            val ok = task.check(reply.toString())
            if (ok && task.name.startsWith("copy")) copied++ else if (ok) passed++
            say(
                JSONObject().put("event", "task").put("task", task.name).put("pass", ok).put("reply", reply.toString().take(900))
                    .put("tokensPerSecond", result.stats.outputTokensPerSecond),
            )
        }
        val condition = phoneCondition(targetContext)
        say(
            JSONObject().put("event", "quality").put("copied", copied).put("copyTasks", COPY_TASKS.size).put("passed", passed)
                .put("tasks", QUALITY_TASKS.size).put("thermal", condition.thermalStatus).put("powerSave", condition.powerSave)
                .put("batteryC", batteryCelsius(targetContext)),
        )
    }

    /** The most memory this process held and the least the phone had free while a model was measured. */
    private inner class MemoryWatch {
        @Volatile private var running = true
        @Volatile private var rss = 0L
        @Volatile private var pss = 0L
        @Volatile private var graphics = 0L
        @Volatile private var least = Long.MAX_VALUE
        private val base = phoneRoom(targetContext, 0).availableMemory
        private val sampler = thread(name = "memory-watch") {
            var tick = 0
            while (running) {
                sample(tick++ % 4 == 0)
                Thread.sleep(500)
            }
        }

        private fun sample(full: Boolean) {
            File("/proc/self/status").readLines().firstOrNull { it.startsWith("VmRSS:") }
                ?.let { rss = maxOf(rss, it.filter(Char::isDigit).toLong() * 1024) }
            least = minOf(least, phoneRoom(targetContext, 0).availableMemory)
            if (full) {
                val info = Debug.MemoryInfo()
                Debug.getMemoryInfo(info)
                pss = maxOf(pss, info.totalPss * 1024L)
                graphics = maxOf(graphics, (info.getMemoryStat("summary.graphics")?.toLong() ?: 0) * 1024)
            }
        }

        fun report(): JSONObject {
            sample(true)
            return JSONObject().put("peakRss", rss).put("peakPss", pss).put("peakGraphics", graphics)
                .put("availableAtStart", base).put("leastAvailable", least)
        }

        fun stop() {
            running = false
            sampler.join()
        }
    }
}
