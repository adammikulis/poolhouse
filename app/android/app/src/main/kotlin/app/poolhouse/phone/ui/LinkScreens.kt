package app.poolhouse.phone.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.poolhouse.core.net.Link
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.R
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.PrimaryButton
import app.poolhouse.phone.ui.components.SecondaryButton
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.phone.ui.theme.Navy
import app.poolhouse.phone.ui.theme.Pool

@Composable
private fun Mark() {
    Box(Modifier.size(96.dp).clip(RoundedCornerShape(22.dp)).background(Navy), contentAlignment = Alignment.Center) {
        Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = "Poolhouse", modifier = Modifier.size(130.dp))
    }
}

/** The first screen of an unlinked phone: scan the computer's code, or type it. */
@Composable
fun LinkScreen(model: PhoneViewModel, error: String) {
    var typing by rememberSaveable { mutableStateOf(false) }
    var address by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    var name by rememberSaveable { mutableStateOf(model.defaultName()) }
    BackHandler { model.cancelLinking() }
    Column(
        Modifier.fillMaxSize().navigationBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Bottom,
    ) {
        Spacer(Modifier.height(24.dp))
        Mark()
        Spacer(Modifier.height(16.dp))
        Text("Link to your computer", style = MaterialTheme.typography.headlineSmall, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(
            "On the computer, open Phones in Poolhouse (or run poolhouse phone link). It shows a code that works once, for ten minutes.",
            style = MaterialTheme.typography.bodyLarge, color = Pool.colors.muted, textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(24.dp))
        if (error.isNotEmpty()) {
            Card { Text(error, style = MaterialTheme.typography.bodyMedium, color = Pool.colors.bad) }
            Spacer(Modifier.height(12.dp))
        }
        if (typing) {
            OutlinedTextField(address, { address = it }, label = { Text("Computer address") }, placeholder = { Text("192.168.1.4") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
            OutlinedTextField(code, { code = it.uppercase().take(16) }, label = { Text("Code") }, placeholder = { Text("ABCDE-FGHJK") }, singleLine = true,
                textStyle = Pool.mono.copy(fontSize = 18.sp), keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false),
                modifier = Modifier.fillMaxWidth())
            OutlinedTextField(name, { name = it.take(48) }, label = { Text("Name of this phone") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            PrimaryButton("Link", { model.typed(address, code, name) }, Modifier.fillMaxWidth(), enabled = address.isNotBlank() && code.isNotBlank())
            Spacer(Modifier.height(8.dp))
            SecondaryButton("Scan the code instead", { typing = false; model.scan() }, Modifier.fillMaxWidth())
        } else {
            PrimaryButton("Scan the code", model::scan, Modifier.fillMaxWidth(), icon = Icons.Scan)
            Spacer(Modifier.height(8.dp))
            SecondaryButton("Type the code instead", { typing = true }, Modifier.fillMaxWidth())
        }
        Spacer(Modifier.height(8.dp))
        TextButton({ model.cancelLinking() }, Modifier.fillMaxWidth()) { Text("Not now", color = Pool.colors.muted) }
    }
}

@Composable
fun LinkingScreen(computer: String, onCancel: () -> Unit) {
    Column(Modifier.fillMaxSize().navigationBarsPadding().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        CircularProgressIndicator(color = Pool.colors.accent)
        Spacer(Modifier.height(20.dp))
        Text("Linking to $computer", style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
        Text("Checking that the computer knows the code.", style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted, textAlign = TextAlign.Center)
        Spacer(Modifier.height(24.dp))
        SecondaryButton("Cancel", onCancel)
    }
}

/** Linked: the words of this phone's key, to be compared with the computer's. */
@Composable
fun ConfirmScreen(link: Link, onDone: () -> Unit) {
    Column(Modifier.fillMaxSize().navigationBarsPadding().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Bottom) {
        Mark()
        Spacer(Modifier.height(16.dp))
        Text("Linked to ${link.computer}", style = MaterialTheme.typography.headlineSmall, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text("The computer's Phones screen shows these words for this phone. If they differ, remove the phone there.", style = MaterialTheme.typography.bodyLarge,
            color = Pool.colors.muted, textAlign = TextAlign.Center)
        Spacer(Modifier.height(20.dp))
        Card { Text(link.words, style = Pool.mono.copy(fontSize = 20.sp, lineHeight = 30.sp), textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
        Spacer(Modifier.weight(1f, fill = false).height(32.dp))
        PrimaryButton("They match", onDone, Modifier.fillMaxWidth(), icon = Icons.Check)
    }
}
