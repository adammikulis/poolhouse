package app.poolhouse.theme

import android.content.Context
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.LocalActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb

enum class ThemeChoice(val label: String) {
    SYSTEM("System"), LIGHT("Light"), DARK("Dark");

    val dark: Boolean
        @Composable @ReadOnlyComposable get() = when (this) {
            SYSTEM -> isSystemInDarkTheme()
            LIGHT -> false
            DARK -> true
        }
}

/** The theme the person chose, kept in one place for every screen of the app, whichever module draws it. */
object ThemePreference {
    private fun store(context: Context) = context.getSharedPreferences("poolhouse", Context.MODE_PRIVATE)

    fun read(context: Context): ThemeChoice =
        ThemeChoice.entries.firstOrNull { it.name == store(context).getString("theme", null) } ?: ThemeChoice.SYSTEM

    fun write(context: Context, choice: ThemeChoice) = store(context).edit().putString("theme", choice.name).apply()
}

/** A whole screen of a module's own activity: the chosen theme, the system bars to match, and room left for them. */
@Composable
fun PoolhouseScreen(choice: ThemeChoice, content: @Composable () -> Unit) {
    val dark = choice.dark
    val activity = LocalActivity.current as? ComponentActivity
    DisposableEffect(dark) {
        val clear = Color.Transparent.toArgb()
        val bars = if (dark) SystemBarStyle.dark(clear) else SystemBarStyle.light(clear, clear)
        activity?.enableEdgeToEdge(statusBarStyle = bars, navigationBarStyle = bars)
        onDispose {}
    }
    PoolhouseTheme(dark) {
        Surface(Modifier.fillMaxSize(), color = Pool.colors.bg, contentColor = Pool.colors.text) {
            Box(Modifier.safeDrawingPadding()) { content() }
        }
    }
}
