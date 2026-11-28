/** Chat with the model on the phone. */
package app.poolhouse.llm.ui

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import app.poolhouse.llm.android.CONTEXT_CHOICES
import app.poolhouse.llm.android.LocalModels
import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.Role
import java.io.ByteArrayOutputStream

/** The longest side a picture is sent with: more than the models' picture encoders read, and bounded in memory. */
private const val LONGEST_SIDE = 2048

class LocalChatActivity : ModelScreen() {
    @Composable
    override fun Content() {
        val context = LocalContext.current
        val models = remember { LocalModels.of(context) }
        // Read again on each return, since the models screen changes what is downloaded and chosen.
        var seen by remember { mutableIntStateOf(0) }
        LifecycleResumeEffect(Unit) {
            seen++
            onPauseOrDispose {}
        }
        val model = remember(seen) { models.chosen() }
        var size by remember(seen) { mutableIntStateOf(model?.let(models::contextFor) ?: 0) }
        var processor by remember(seen) { mutableStateOf(models.accelerator) }
        val pictures = remember { mutableStateListOf<ByteArray>() }
        val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
            uri?.let { picture(context, it) }?.let { pictures += it }
        }
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(model?.title ?: "No model downloaded", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                TextButton(onClick = { startActivity(Intent(context, ModelsActivity::class.java)) }) { Text("Models") }
            }
            if (model == null) {
                Text("Download a model once. After that it answers here with no computer and no network.")
                Button(onClick = { startActivity(Intent(context, ModelsActivity::class.java)) }) { Text("Choose a model") }
                return@Column
            }
            val used = ChatSession.last?.contextUsed ?: 0
            Text(
                "Answers on this phone, with no computer. Context $used of $size tokens" +
                    (ChatSession.accelerator?.let { " · ${it.label}" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Choice("Context $size", CONTEXT_CHOICES.filter { it <= model.contextLimit }.map { "$it tokens" to it }, !ChatSession.busy) {
                    models.contextTokens = it
                    size = models.contextFor(model)
                }
                if (!model.gpuOnly) {
                    Choice("On the ${processor.label}", Accelerator.entries.map { it.label to it }, !ChatSession.busy) {
                        models.accelerator = it
                        processor = it
                    }
                }
                TextButton(onClick = ChatSession::clear) { Text("New chat") }
            }
            val list = rememberLazyListState()
            val count = ChatSession.lines.size + if (ChatSession.writing != null) 1 else 0
            LaunchedEffect(count, ChatSession.writing?.length) {
                if (count > 0) list.scrollToItem(count - 1, Int.MAX_VALUE)
            }
            LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(ChatSession.lines) { line -> Message(line) }
                ChatSession.writing?.takeIf { it.isNotEmpty() }?.let { text -> item { Card { Text(text, Modifier.padding(12.dp)) } } }
                if (count == 0) item { Text("Chats stay in memory and are gone when the app closes.") }
            }
            if (ChatSession.status.isNotEmpty()) Text(ChatSession.status, style = MaterialTheme.typography.bodySmall)
            if (pictures.isNotEmpty()) {
                TextButton(onClick = { pictures.clear() }) { Text("${pictures.size} picture(s) attached · Remove") }
            }
            OutlinedTextField(
                value = ChatSession.draft,
                onValueChange = { ChatSession.draft = it },
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("Ask the model on this phone") },
                maxLines = 5,
            )
            Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                if (model.images) {
                    TextButton(
                        enabled = !ChatSession.busy,
                        onClick = { picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                    ) { Text("Add picture") }
                } else {
                    Text("This model reads text only.", style = MaterialTheme.typography.bodySmall)
                }
                if (ChatSession.busy) {
                    Button(onClick = ChatSession::stop) { Text("Stop") }
                } else {
                    Button(
                        enabled = ChatSession.draft.isNotBlank() || pictures.isNotEmpty(),
                        onClick = {
                            val text = ChatSession.draft
                            val sent = pictures.toList()
                            ChatSession.draft = ""
                            pictures.clear()
                            ChatSession.send(models, model, text, sent)
                        },
                    ) { Text("Send") }
                }
            }
        }
    }

    @Composable
    private fun Message(line: Line) {
        Column(Modifier.fillMaxWidth()) {
            Text(if (line.turn.role == Role.USER) "You" else "Model", style = MaterialTheme.typography.labelMedium)
            line.turn.images.forEach { bytes ->
                val shown = remember(bytes) { BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() }
                shown?.let { Image(it, contentDescription = "Attached picture", modifier = Modifier.height(160.dp)) }
            }
            SelectionContainer { Text(line.turn.text) }
            line.note?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

@Composable
private fun <T> Choice(label: String, options: List<Pair<String, T>>, enabled: Boolean, chosen: (T) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Column {
        TextButton(enabled = enabled, onClick = { open = true }) { Text(label) }
        DropdownMenu(open, onDismissRequest = { open = false }) {
            options.forEach { (name, value) ->
                DropdownMenuItem(text = { Text(name) }, onClick = { open = false; chosen(value) })
            }
        }
    }
}

/** The picked picture as a JPEG no longer than [LONGEST_SIDE] on a side, or null when it cannot be read. */
private fun picture(context: Context, uri: Uri): ByteArray? = runCatching {
    val bitmap = ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, uri)) { decoder, info, _ ->
        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        val longest = maxOf(info.size.width, info.size.height)
        if (longest > LONGEST_SIDE) {
            decoder.setTargetSize(info.size.width * LONGEST_SIDE / longest, info.size.height * LONGEST_SIDE / longest)
        }
    }
    ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.JPEG, 92, it) }.toByteArray()
}.getOrNull()
