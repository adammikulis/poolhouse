/** The models the phone can hold: what fits, what a download costs, its licence, and removing it. */
package app.poolhouse.llm.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.poolhouse.llm.android.DownloadService
import app.poolhouse.llm.android.DownloadState
import app.poolhouse.llm.android.LocalModels
import app.poolhouse.llm.android.metered
import app.poolhouse.llm.android.phoneRoom
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.model.CatalogModel
import app.poolhouse.llm.model.gigabytes
import app.poolhouse.llm.model.verdict
import kotlin.concurrent.thread

class ModelsActivity : ModelScreen() {
    @Composable
    override fun Content() {
        val context = LocalContext.current
        val models = remember { LocalModels.of(context) }
        var changed by remember { mutableIntStateOf(0) }
        val download by DownloadService.state.collectAsState()
        var asking by remember { mutableStateOf<Pair<String, CatalogModel>?>(null) }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                val room = remember(changed, download?.running) { phoneRoom(context, models.store.freeSpace()) }
                Text("Phone models", style = MaterialTheme.typography.headlineSmall)
                Text(
                    "${gigabytes(room.availableMemory)} of ${gigabytes(room.totalMemory)} memory free · " +
                        "${gigabytes(room.freeStorage)} storage free. Nothing downloads until you ask.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            items(Catalog.models, key = { it.id }) { model ->
                val mine = download?.takeIf { it.model == model.id }
                // `changed` and the download's progress make the stored size and the verdict current.
                val stored = remember(changed, mine) { models.store.stored(model) }
                val installed = remember(changed, mine) { models.store.installed(model) }
                Entry(models, model, stored, installed, mine, download?.running == true, changed) { action ->
                    when (action) {
                        "download" -> if (metered(context)) asking = "metered" to model else DownloadService.start(context, model.id)
                        "stop" -> DownloadService.stop()
                        "use" -> models.choose(model)
                        "delete" -> asking = "delete" to model
                    }
                    changed++
                }
            }
        }
        asking?.let { (question, model) ->
            val deleting = question == "delete"
            AlertDialog(
                onDismissRequest = { asking = null },
                title = { Text(if (deleting) "Delete ${model.title}?" else "This network is metered") },
                text = {
                    Text(
                        if (deleting) "Its ${gigabytes(models.store.stored(model))} leave the phone. You can download it again."
                        else "${model.title} is ${gigabytes(model.bytes - models.store.stored(model))} to download, and this connection may cost money.",
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        asking = null
                        if (deleting) {
                            // Waits for a running reply to end before the files go.
                            thread { models.delete(model); runOnUiThread { changed++ } }
                        } else {
                            DownloadService.start(context, model.id)
                        }
                    }) { Text(if (deleting) "Delete" else "Download anyway") }
                },
                dismissButton = { TextButton(onClick = { asking = null }) { Text("Cancel") } },
            )
        }
    }

    @Composable
    private fun Entry(
        models: LocalModels,
        model: CatalogModel,
        stored: Long,
        installed: Boolean,
        download: DownloadState?,
        anyRunning: Boolean,
        changed: Int,
        act: (String) -> Unit,
    ) {
        val context = LocalContext.current
        val verdict = remember(changed, stored) {
            verdict(model, phoneRoom(context, models.store.freeSpace()), stored, models.measuredMemory(model))
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(model.title, style = MaterialTheme.typography.titleMedium)
                Text(model.about)
                Text(
                    "${gigabytes(model.bytes)} · LiteRT-LM · up to ${model.contextLimit} tokens · " +
                        if (model.images) "reads pictures" else "text only",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(verdict.words, style = MaterialTheme.typography.bodySmall)
                Row {
                    TextButton(onClick = { open(model.licence.url) }) { Text("Licence: ${model.licence.name}") }
                    TextButton(onClick = { open(model.page) }) { Text("Source") }
                }
                download?.failure?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                if (download?.running == true) {
                    LinearProgressIndicator(progress = { download.held.toFloat() / model.bytes }, modifier = Modifier.fillMaxWidth())
                    Text("${gigabytes(download.held)} of ${gigabytes(model.bytes)}", style = MaterialTheme.typography.bodySmall)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    when {
                        download?.running == true -> Button(onClick = { act("stop") }) { Text("Stop") }
                        installed && models.chosen()?.id == model.id -> Text("In use", Modifier.padding(vertical = 12.dp))
                        installed -> Button(onClick = { act("use") }) { Text("Use") }
                        else -> Button(enabled = !anyRunning && verdict.downloadable, onClick = { act("download") }) {
                            Text(if (stored > 0) "Continue download" else "Download ${gigabytes(model.bytes)}")
                        }
                    }
                    if (stored > 0 && download?.running != true) TextButton(onClick = { act("delete") }) { Text("Delete") }
                }
            }
        }
    }

    private fun open(address: String) {
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(address))) }
    }
}
