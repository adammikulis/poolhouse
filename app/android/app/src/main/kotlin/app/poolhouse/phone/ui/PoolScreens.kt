package app.poolhouse.phone.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.poolhouse.core.api.Device
import app.poolhouse.core.device.WorkerState
import app.poolhouse.core.store.PoolState
import app.poolhouse.core.store.ago
import app.poolhouse.core.store.bytes
import app.poolhouse.core.theme.Section
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.PoolSession
import app.poolhouse.phone.Screen
import app.poolhouse.phone.platform.phoneFacts
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.Dot
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.components.Fact
import app.poolhouse.phone.ui.components.Meter
import app.poolhouse.phone.ui.components.SecondaryButton
import app.poolhouse.phone.ui.components.SectionLabel
import app.poolhouse.phone.ui.components.Tag
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.phone.ui.theme.Pool
import app.poolhouse.phone.ui.theme.tint

private fun Device.isThisPhone(phone: String): Boolean = id == "phone:$phone"

@Composable
private fun deviceState(d: Device): Pair<String, androidx.compose.ui.graphics.Color> = when {
    !d.online -> "offline" to Pool.colors.muted
    d.paused -> "paused" to Pool.colors.warn
    d.busy -> "busy" to Pool.colors.busy
    else -> "online" to Pool.colors.good
}

/**
 * The one switch of this phone (docs/phone-api.md section 5), off until the person turns it on, with what it does now. It lends
 * the on-device model to a linked computer's pool; joining a pool as a node on its own is not built, and the card says so rather
 * than claiming it. A switch left on can always be turned off.
 */
@Composable
fun ThisPhoneCard(model: PhoneViewModel, pool: PoolSession?, state: PoolState?, modifier: Modifier = Modifier) {
    val worker = model.worker
    val local: WorkerState? = worker?.state?.collectAsState()?.value
    val computerSays = state?.poolUse?.value?.enabled
    val linked = pool != null
    val on = local?.enabled ?: (computerSays == true)
    Card(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Phone, null, tint = Section.MODELS.tint(), modifier = Modifier.size(22.dp))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text("This phone", style = MaterialTheme.typography.titleMedium)
                val standing = when {
                    on && linked -> "Part of the pool"
                    on -> "Switched on"
                    linked -> "Directing only"
                    else -> "On its own"
                }
                Text(standing, style = MaterialTheme.typography.bodySmall, color = if (on) Pool.colors.good else Pool.colors.muted)
            }
            if ((worker != null && (linked || on)) || (worker == null && linked && on)) {
                Switch(
                    checked = on,
                    onCheckedChange = { want -> if (worker != null) worker.setEnabled(want) else if (!want) pool?.store?.endPoolUse() },
                    enabled = state == null || "pool-use" !in state.busy,
                    colors = SwitchDefaults.colors(checkedTrackColor = Pool.colors.accent, checkedThumbColor = Pool.colors.onAccent),
                    modifier = Modifier.semantics { contentDescription = "Let the pool use this phone as a worker or coding agent" },
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        val explain = when {
            local != null && (linked || on) -> local.detail
            !linked -> "This phone is not part of a pool. Joining one on its own is not in this build; link a computer to lend it this phone's model."
            on -> "The computer says the pool may use this phone, and this build has no on-device model to answer it."
            else -> "The pool sends this phone no work. This build has no on-device model, so there is nothing to switch on."
        }
        Text(explain, style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted)
        if (local != null && (linked || on)) {
            local.blocked?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Pool.colors.warn, modifier = Modifier.padding(top = 4.dp)) }
            local.model?.let { Text(it, style = Pool.mono, modifier = Modifier.padding(top = 4.dp)) }
            local.cost?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(top = 4.dp)) }
        }
        if (local != null && computerSays != null && computerSays != local.enabled) {
            Text(if (computerSays) "The computer still shows it on." else "The computer still shows it off.", style = MaterialTheme.typography.bodySmall, color = Pool.colors.warn, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

/** What Android says about this phone: shown on the devices screen with or without a computer. */
@Composable
fun PhoneFactsCard(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val now = rememberNow(5_000)
    val facts = remember(now) { phoneFacts(context) }
    Card(modifier) {
        Fact("Device", facts.name)
        Fact("System", facts.android)
        Fact("Chip", facts.chip)
        Fact("Cores", "${facts.cores}")
        if (facts.memoryTotal > 0) Measure("Memory", facts.memoryUsed.toFloat() / facts.memoryTotal, "${bytes(facts.memoryUsed)} of ${bytes(facts.memoryTotal)}")
        if (facts.storageTotal > 0) {
            val used = facts.storageTotal - facts.storageFree
            Measure("Storage", used.toFloat() / facts.storageTotal, "${bytes(facts.storageFree)} free")
        }
    }
}

@Composable
fun PoolScreen(model: PhoneViewModel, pool: PoolSession?, state: PoolState?) {
    if (pool == null || state == null) PhonePool(model) else LinkedPool(model, pool, state)
}

/** The devices screen of an unlinked phone: this phone, its models, and the offer to link. */
@Composable
private fun PhonePool(model: PhoneViewModel) {
    Column(Modifier.fillMaxSize()) {
        TopBar("Pool", subtitle = "This phone") {
            IconButton({ model.open(Screen.Settings) }) { Icon(Icons.Settings, "Settings", tint = Pool.colors.text) }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                ThisPhoneCard(model, null, null)
                PhoneFactsCard()
            }
            SectionLabel("Models on this phone")
            PhoneEntries(model)
            LinkOfferCard(model, Modifier.padding(12.dp))
        }
    }
}

@Composable
private fun LinkedPool(model: PhoneViewModel, pool: PoolSession, state: PoolState) {
    val devices = state.devices.value
    val now = rememberNow()
    val computers = devices?.devices.orEmpty().filter { !it.isThisPhone(pool.link.phone) }
    Column(Modifier.fillMaxSize()) {
        TopBar("Pool", subtitle = listOfNotNull(devices?.pool?.name?.ifBlank { null }, connectionLine(state).ifBlank { null }).joinToString(" · ")) {
            IconButton({ model.open(Screen.Settings) }) { Icon(Icons.Settings, "Settings", tint = Pool.colors.text) }
        }
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 88.dp)) {
            item {
                ThisPhoneCard(model, pool, state, Modifier.padding(start = 12.dp, end = 12.dp, top = 12.dp))
                Text("This phone's details", style = MaterialTheme.typography.labelLarge, color = Pool.colors.accentInk,
                    modifier = Modifier.clickable { model.open(Screen.Device("phone:${pool.link.phone}")) }.padding(horizontal = 16.dp, vertical = 10.dp))
            }
            if (state.devices.error.isNotEmpty()) item { Text(state.devices.error, color = Pool.colors.bad, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(16.dp)) }
            if (devices == null) {
                if (state.devices.error.isEmpty()) item { EmptyState("Reading the pool", "Waiting for ${state.computer.ifEmpty { "the computer" }}.") }
            } else {
                item { Summary(computers) }
                item { SectionLabel("Devices (${computers.size})") }
                items(computers, key = { it.id }) { device ->
                    DeviceCard(device, now, Modifier.padding(horizontal = 12.dp, vertical = 5.dp)) { model.open(Screen.Device(device.id)) }
                }
                item {
                    Row(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        val busy = "pool" in state.busy
                        if (computers.any { it.paused }) SecondaryButton("Resume the pool", { pool.store.resumePool() }, Modifier.weight(1f), working = busy)
                        else SecondaryButton("Pause the pool for 2 h", { pool.store.pausePool("2h") }, Modifier.weight(1f), working = busy)
                    }
                }
                if (devices.note.isNotBlank()) item { Text(devices.note, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(16.dp)) }
            }
        }
    }
}

@Composable
private fun Summary(devices: List<Device>) {
    val online = devices.count { it.online }
    val slots = devices.filter { it.online }.sumOf { it.slots }
    val free = devices.filter { it.online }.sumOf { it.free }
    val models = devices.filter { it.online }.flatMap { it.serving }.size
    Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        for ((value, label) in listOf("$online/${devices.size}" to "online", "$free/$slots" to "slots free", "$models" to "serving")) {
            Card(Modifier.weight(1f)) {
                Text(value, style = Pool.mono.copy(fontSize = MaterialTheme.typography.titleLarge.fontSize, lineHeight = MaterialTheme.typography.titleLarge.lineHeight))
                Text(label.uppercase(), style = MaterialTheme.typography.labelSmall, color = Pool.colors.muted, maxLines = 1)
            }
        }
    }
}

@Composable
private fun DeviceCard(d: Device, now: Double, modifier: Modifier, onOpen: () -> Unit) {
    val (word, color) = deviceState(d)
    Card(modifier, onClick = onOpen) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Dot(color)
            Spacer(Modifier.width(8.dp))
            Text(d.name.ifBlank { d.id }, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (d.thisComputer) Tag("Linked", Pool.colors.accentInk)
            if (d.kind == "phone") Tag("Phone")
            Spacer(Modifier.width(6.dp))
            Tag(word, color)
        }
        val facts = listOf(d.gpu, d.room, if (d.online && d.slots > 0) "${d.free} of ${d.slots} slots free" else "", if (d.queued > 0) "${d.queued} queued" else "",
            if (!d.online) ago(d.lastSeen, now).let { if (it.isEmpty()) "" else if (it == "now") "seen just now" else "seen $it ago" } else "")
        val line = facts.filter { it.isNotBlank() }.joinToString(" · ")
        if (line.isNotEmpty()) Text(line, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(top = 4.dp))
        if (d.paused && d.pausedBecause.isNotBlank()) Text("Paused: ${d.pausedBecause}", style = MaterialTheme.typography.bodySmall, color = Pool.colors.warn)
        Load(d)
        val serving = d.serving.ifEmpty { if (d.kind == "phone") d.models else emptyList() }
        for (name in serving.take(3)) Text(name, style = Pool.mono, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
        if (serving.size > 3) Text("and ${serving.size - 3} more", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
        if (d.kind == "phone") Text(if (d.poolUse) "Part of the pool" else "Directing only", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(top = 4.dp))
    }
}

/** The measured load a computer reports; nothing is drawn for what it does not report. */
@Composable
private fun Load(d: Device) {
    val used = d.memory?.usedBytes
    val total = d.memory?.totalBytes
    if (used != null && total != null && total > 0) Measure("Memory", used.toFloat() / total, "${bytes(used)} of ${bytes(total)}")
    d.cpuLoad?.let { Measure("CPU", it.toFloat(), "${(it * 100).toInt()}%") }
    for (gpu in d.gpus) {
        gpu.load?.let { Measure(gpu.name.ifBlank { "GPU" }, it.toFloat(), "${(it * 100).toInt()}%") }
        val held = gpu.memoryUsedBytes
        val room = gpu.memoryTotalBytes
        if (held != null && room != null && room > 0) Measure("${gpu.name.ifBlank { "GPU" }} memory", held.toFloat() / room, "${bytes(held)} of ${bytes(room)}")
    }
}

@Composable
private fun Measure(name: String, fraction: Float, value: String) {
    Column(Modifier.padding(top = 8.dp)) {
        Row {
            Text(name, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.weight(1f), maxLines = 1)
            Text(value, style = Pool.mono)
        }
        Spacer(Modifier.height(3.dp))
        Meter(fraction, if (fraction > 0.9f) Pool.colors.bad else Pool.colors.good)
    }
}

@Composable
fun DeviceScreen(model: PhoneViewModel, pool: PoolSession, state: PoolState, id: String) {
    val d = state.devices.value?.devices?.firstOrNull { it.id == id }
    val mine = id == "phone:${pool.link.phone}"
    val now = rememberNow()
    Column(Modifier.fillMaxSize()) {
        TopBar(d?.name?.ifBlank { d.id } ?: if (mine) "This phone" else "Device", subtitle = d?.let { deviceState(it).first } ?: "", onBack = model::back)
        if (d == null && !mine) {
            EmptyState("Gone from the pool", "The computer no longer lists this device.")
            return@Column
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (mine) {
                ThisPhoneCard(model, pool, state)
                PhoneFactsCard()
            }
            if (d == null) return@Column
            Card {
                Fact("Kind", if (d.thisComputer) "Computer (linked)" else d.kind.replaceFirstChar { it.uppercase() })
                Fact("State", deviceState(d).first.replaceFirstChar { it.uppercase() } + if (d.paused && d.pausedBecause.isNotBlank()) ": ${d.pausedBecause}" else "")
                Fact("Last seen", if (d.online) "Online now" else ago(d.lastSeen, now).let { if (it.isEmpty()) "Not known" else if (it == "now") "Just now" else "$it ago" })
                // The computer names no address for itself: the ones the phone reaches it by are shown instead.
                Fact("Address", d.address.ifBlank { if (d.thisComputer) pool.link.addresses.joinToString("\n") else "" }, mono = true)
                Fact("GPU", d.gpu)
                Fact("Room", d.room)
                Fact("Slots", if (d.slots > 0) "${d.free} free of ${d.slots}, ${d.queued} queued" else "")
                Fact("Version", d.version)
                Fact("Id", d.id, mono = true)
                Load(d)
            }
            if (d.serving.isNotEmpty() || d.models.isNotEmpty()) {
                Card {
                    Text("Serving", style = MaterialTheme.typography.titleSmall)
                    if (d.serving.isEmpty()) Text("Nothing", style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted)
                    for (name in d.serving) Text(name, style = Pool.mono, modifier = Modifier.padding(top = 4.dp))
                    val others = d.models.filter { m -> d.serving.none { it.startsWith(m) } }
                    if (others.isNotEmpty()) {
                        Text("Also has", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 10.dp))
                        for (name in others) Text(name, style = Pool.mono, color = Pool.colors.muted, modifier = Modifier.padding(top = 4.dp))
                    }
                }
            }
            // Serving is directed at the linked computer: that is what the phone API starts and stops.
            if (d.thisComputer) ModelsCard(pool, state)
            if (!d.thisComputer && d.kind == "computer") {
                Text("To serve or stop a model on this computer, link the phone to it.", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted,
                    modifier = Modifier.padding(horizontal = 4.dp).clickable { model.open(Screen.Settings) })
            }
        }
    }
}

/** The linked computer's models: what runs, with Stop, and what is installed, with Serve. */
@Composable
fun ModelsCard(pool: PoolSession, state: PoolState) {
    val models = state.models.value
    Card {
        Text("Models on ${state.computer.ifEmpty { "this computer" }}", style = MaterialTheme.typography.titleSmall)
        if (state.models.error.isNotEmpty()) Text(state.models.error, style = MaterialTheme.typography.bodySmall, color = Pool.colors.bad)
        if (models == null) {
            if (state.models.error.isEmpty()) Text("Reading…", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
            return@Card
        }
        for (run in models.running) {
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(run.models.joinToString(", ").ifBlank { "Model" }, style = Pool.mono)
                    Text("serving on port ${run.port}", style = MaterialTheme.typography.bodySmall, color = Pool.colors.good)
                }
                SecondaryButton("Stop", { pool.store.stopModel(run.port) }, danger = true, working = "model:${run.port}" in state.busy)
            }
        }
        if (!models.canServe) Text("This computer cannot serve a model now.", style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, modifier = Modifier.padding(top = 8.dp))
        for (file in models.installed) {
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f).padding(end = 8.dp)) {
                    Text(file.name.ifBlank { file.path }, style = Pool.mono)
                    if (file.sizeBytes > 0) Text(bytes(file.sizeBytes), style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted)
                }
                if (models.canServe) SecondaryButton("Serve", { pool.store.startModel(file.path, file.name.ifBlank { file.path }, null) }, working = "model:${file.path}" in state.busy)
            }
        }
        if (models.installed.isEmpty() && models.running.isEmpty()) Text("No model is installed.", style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted)
    }
}
