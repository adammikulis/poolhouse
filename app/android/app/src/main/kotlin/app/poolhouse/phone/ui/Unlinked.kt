package app.poolhouse.phone.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.poolhouse.phone.PhoneViewModel
import app.poolhouse.phone.ui.components.Card
import app.poolhouse.phone.ui.components.EmptyState
import app.poolhouse.phone.ui.components.PrimaryButton
import app.poolhouse.phone.ui.components.TopBar
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.phone.ui.theme.Pool

/** The offer to link a computer, shown wherever it would add something. It is an offer: the app works without it. */
@Composable
fun LinkOfferCard(model: PhoneViewModel, modifier: Modifier = Modifier) {
    Card(modifier) {
        Text("A computer adds the rest", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        Text("Optional. Talk to its Claude sessions and agents, see your pool's devices, follow work and serve models from this phone.",
            style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted)
        Spacer(Modifier.height(10.dp))
        PrimaryButton("Link a computer", model::offerLink, Modifier.fillMaxWidth(), icon = Icons.Scan)
    }
}

/** A tab whose content lives on a computer, with no computer linked. */
@Composable
fun NeedsComputer(model: PhoneViewModel, title: String, what: String) {
    Column(Modifier.fillMaxSize()) {
        TopBar(title, subtitle = "This phone")
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp)) {
            EmptyState("$title needs a computer", what)
            LinkOfferCard(model)
        }
    }
}
