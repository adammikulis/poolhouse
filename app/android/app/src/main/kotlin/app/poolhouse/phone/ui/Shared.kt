package app.poolhouse.phone.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import app.poolhouse.core.store.Connection
import app.poolhouse.core.store.PoolState
import kotlinx.coroutines.delay

/** The time in Unix seconds, moving while the screen shows ages and countdowns. */
@Composable
fun rememberNow(everyMs: Long = 15_000): Double {
    val now by produceState(System.currentTimeMillis() / 1000.0, everyMs) {
        while (true) {
            delay(everyMs)
            value = System.currentTimeMillis() / 1000.0
        }
    }
    return now
}

/** The second line of a tab's header: the computer and whether the phone hears it. */
fun connectionLine(state: PoolState): String {
    val how = when (state.connection) {
        Connection.Online -> "live"
        Connection.Connecting -> "connecting"
        is Connection.Offline -> "offline"
        is Connection.Unlinked -> "removed"
    }
    return listOf(state.computer, how).filter { it.isNotBlank() }.joinToString(" · ")
}
