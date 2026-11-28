package app.poolhouse.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import app.poolhouse.core.theme.Brand
import app.poolhouse.core.theme.Palette
import app.poolhouse.core.theme.Palettes
import app.poolhouse.core.theme.Section

/** The web UI's tokens as Compose colours. Screens read these; Material's scheme is filled from the same values. */
data class PoolColors(
    val dark: Boolean,
    val bg: Color,
    val surface: Color,
    val sunken: Color,
    val line: Color,
    val lineStrong: Color,
    val text: Color,
    val muted: Color,
    val accent: Color,
    val accentInk: Color,
    val accentDim: Color,
    val onAccent: Color,
    val good: Color,
    val bad: Color,
    /** Warnings and work in progress, readable as text on the background of each theme. */
    val warn: Color,
    val busy: Color,
)

private fun colors(p: Palette, dark: Boolean) = PoolColors(
    dark = dark,
    bg = Color(p.bg),
    surface = Color(p.surface),
    sunken = Color(p.sunken),
    line = Color(p.line),
    lineStrong = Color(p.lineStrong),
    text = Color(p.text),
    muted = Color(p.muted),
    accent = Color(p.accent),
    accentInk = Color(p.accentInk),
    accentDim = Color(p.accentDim),
    onAccent = Color(p.onAccent),
    good = Color(p.green),
    bad = Color(p.red),
    warn = Color(if (dark) p.yellow else p.warn),
    busy = Color(p.busy),
)

val Navy = Color(Brand.NAVY)
val NavyInk = Color(Brand.NAVY_INK)
val RailText = Color(Brand.RAIL_TEXT)

fun Section.tint(): Color = Color(color)

private val LocalPoolColors = staticCompositionLocalOf { colors(Palettes.light, false) }

object Pool {
    val colors: PoolColors
        @Composable @ReadOnlyComposable get() = LocalPoolColors.current

    val mono = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 12.5.sp, lineHeight = 18.sp)
}

/** Compact sizes for a screen full of rows: 15 sp body, 13 sp secondary, titles that are tight and heavy like the web's. */
private val Type = Typography().run {
    copy(
        headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.02).em),
        titleLarge = TextStyle(fontSize = 19.sp, lineHeight = 24.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.02).em),
        titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 21.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.01).em),
        titleSmall = TextStyle(fontSize = 15.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
        bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
        bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
        bodySmall = TextStyle(fontSize = 12.5.sp, lineHeight = 17.sp),
        labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold),
        labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.SemiBold),
        labelSmall = TextStyle(fontSize = 10.5.sp, lineHeight = 14.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.1.em),
    )
}

private val Corners = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(10.dp),
    large = RoundedCornerShape(14.dp),
    extraLarge = RoundedCornerShape(22.dp),
)

@Composable
fun PoolhouseTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val c = colors(if (dark) Palettes.dark else Palettes.light, dark)
    val scheme = (if (dark) darkColorScheme() else lightColorScheme()).copy(
        primary = c.accent,
        onPrimary = c.onAccent,
        primaryContainer = c.accentDim,
        onPrimaryContainer = c.text,
        secondary = c.accentInk,
        onSecondary = c.surface,
        secondaryContainer = c.accentDim,
        onSecondaryContainer = c.text,
        background = c.bg,
        onBackground = c.text,
        surface = c.surface,
        onSurface = c.text,
        surfaceVariant = c.sunken,
        onSurfaceVariant = c.muted,
        surfaceContainerLowest = c.surface,
        surfaceContainerLow = c.surface,
        surfaceContainer = c.surface,
        surfaceContainerHigh = c.surface,
        surfaceContainerHighest = c.sunken,
        outline = c.lineStrong,
        outlineVariant = c.line,
        error = c.bad,
        onError = c.surface,
    )
    CompositionLocalProvider(LocalPoolColors provides c) {
        MaterialTheme(colorScheme = scheme, typography = Type, shapes = Corners, content = content)
    }
}
