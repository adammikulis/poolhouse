/**
 * A pool request served on the phone by its real model: the worker, the queue and the engine as the app runs them,
 * against a stand-in for the computer inside this process. It answers two requests, turns the switch off during the
 * second, and then searches everything the app wrote for the words of the first.
 */
package app.poolhouse.llm.bench

import android.content.Context
import app.poolhouse.core.device.ComputerLink
import app.poolhouse.llm.android.LocalModels
import app.poolhouse.llm.android.PhoneLender
import app.poolhouse.llm.android.phoneCondition
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.pool.PoolWorker
import app.poolhouse.llm.pool.SwitchStore
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

private const val COMPUTER = "Bench stand-in"

fun poolBench(context: Context, modelId: String, tokens: Int, say: (JSONObject) -> Unit) {
    val models = LocalModels.of(context)
    val model = Catalog.byId(modelId) ?: throw IllegalArgumentException("No model $modelId.")
    check(models.store.installed(model)) { "The model is not downloaded." }
    models.choose(model)
    models.contextTokens = tokens
    val canary = "CANARY" + System.nanoTime().toString().takeLast(8)
    val posts = CopyOnWriteArrayList<Pair<String, JSONObject>>()
    val states = CopyOnWriteArrayList<String>()
    val firstDone = CountDownLatch(1)
    val secondEnded = CountDownLatch(1)
    var asked = 0
    var secondDeltas = 0
    var turnedOffAt = 0L
    var endedAt = 0L
    lateinit var worker: PoolWorker
    val computer = ComputerLink { op, body, _ ->
        when {
            op == "me" -> JSONObject().put("computer", COMPUTER).toString()
            op == "pool-use" -> JSONObject().put("enabled", body?.let { JSONObject(it).getBoolean("enabled") } ?: true).toString()
            op == "model/next" -> {
                val number = ++asked
                if (number > 2) awaitCancellation()
                val text = if (number == 1) {
                    "Here is one line of a private program:\nkey = \"$canary\"\nWhat string is assigned to key? Reply with the string only."
                } else {
                    "Write a long story about a lighthouse, at least two thousand words."
                }
                val request = JSONObject().put("id", "r-$number").put("model", model.id).put("max_tokens", if (number == 1) 40 else 2048)
                    .put("temperature", 0).put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", text)))
                JSONObject().put("enabled", true).put("request", request).toString()
            }
            else -> {
                val post = JSONObject(body!!)
                val id = op.substringAfterLast('/')
                posts += id to post
                if (id == "r-1" && (post.optBoolean("done") || post.has("error"))) firstDone.countDown()
                if (id == "r-2") {
                    if (post.has("error")) {
                        endedAt = System.nanoTime()
                        secondEnded.countDown()
                    } else if (post.optString("delta").isNotEmpty() && ++secondDeltas == 5) {
                        turnedOffAt = System.nanoTime()
                        worker.setEnabled(false)
                    }
                }
                JSONObject().put("continue", true).toString()
            }
        }
    }
    val store = object : SwitchStore {
        override var enabled = false
        override var unsent = false
    }
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    try {
        // Battery saver is a person's setting this check leaves alone; the gate that heeds it has its own tests.
        val real = phoneCondition(context)
        say(JSONObject().put("event", "condition").put("powerSave", real.powerSave).put("thermal", real.thermalStatus)
            .put("battery", real.batteryPercent).put("charging", real.charging))
        worker = PoolWorker(computer, store, PhoneLender(models), { phoneCondition(context).copy(powerSave = false) }, scope, Dispatchers.IO)
        scope.launch { worker.state.collect { states += "${it.phase} ${it.detail}" } }
        worker.setEnabled(true)
        val answered = firstDone.await(180, TimeUnit.SECONDS)
        val ended = answered && secondEnded.await(180, TimeUnit.SECONDS)
        val first = posts.filter { it.first == "r-1" }
        val reply = first.joinToString("") { it.second.optString("delta") }
        say(
            JSONObject().put("event", "pool").put("answered", answered).put("reply", reply.take(120))
                .put("modelRepeatedCanary", reply.contains(canary)).put("posts", first.size)
                .put("lastFirst", first.lastOrNull()?.second?.toString()?.replace(canary, "[canary]"))
                .put("secondEnded", ended).put("secondDeltas", secondDeltas)
                .put("secondError", posts.lastOrNull { it.first == "r-2" }?.second?.optString("error"))
                .put("offToEndMs", if (ended) (endedAt - turnedOffAt) / 1_000_000 else -1)
                .put("pollsAfterOff", asked - 2).put("states", JSONArray(states.distinct())),
        )
        models.host.release()
        Thread.sleep(1000)
        val found = JSONArray()
        var searched = 0
        context.dataDir.walkTopDown().filter { it.isFile && !it.path.contains("/llm-models/") }.forEach { file ->
            searched++
            if (holds(file, canary.toByteArray())) found.put(file.relativeTo(context.dataDir).path)
        }
        say(JSONObject().put("event", "traces").put("filesSearched", searched).put("filesHoldingCanary", found).put("canary", canary))
    } finally {
        scope.cancel()
    }
}

/** Whether the bytes of `word` appear anywhere in `file`, in UTF-8 or UTF-16. */
private fun holds(file: File, word: ByteArray): Boolean {
    val data = runCatching { file.readBytes() }.getOrNull() ?: return false
    val wide = String(word).toByteArray(Charsets.UTF_16LE)
    return listOf(word, wide).any { needle ->
        (0..data.size - needle.size).any { start -> needle.indices.all { data[start + it] == needle[it] } }
    }
}
