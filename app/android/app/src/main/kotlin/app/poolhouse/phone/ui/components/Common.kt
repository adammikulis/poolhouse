package app.poolhouse.phone.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.poolhouse.core.store.Phase
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.phone.ui.theme.Pool

/** The flat header of every screen: back on a detail, a tight title, a quiet second line and the screen's own buttons. */
@Composable
fun TopBar(title: String, subtitle: String = "", onBack: (() -> Unit)? = null, actions: @Composable RowScope.() -> Unit = {}) {
    Column(Modifier.fillMaxWidth().background(Pool.colors.surface)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = if (onBack == null) 16.dp else 4.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            if (onBack != null) {
                IconButton(onClick = onBack) { Icon(Icons.Back, contentDescription = "Back", tint = Pool.colors.text) }
            }
            Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                Text(
                    title,
                    style = if (onBack == null) MaterialTheme.typography.headlineSmall else MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.semantics { heading() },
                )
                if (subtitle.isNotEmpty()) {
                    Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Pool.colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            actions()
        }
        HorizontalDivider(color = Pool.colors.line)
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        style = MaterialTheme.typography.labelSmall,
        color = Pool.colors.muted,
        modifier = modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 6.dp).semantics { heading() },
    )
}

/** A surface with the web's hairline border and corner. */
@Composable
fun Card(modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val shape = RoundedCornerShape(14.dp)
    Column(
        modifier
            .fillMaxWidth()
            .clip(shape)
            .background(Pool.colors.surface)
            .border(1.dp, Pool.colors.line, shape)
            .then(if (onClick == null) Modifier else Modifier.clickable(onClick = onClick))
            .padding(14.dp),
        content = content,
    )
}

@Composable
fun Tag(text: String, color: Color = Pool.colors.muted, filled: Boolean = false) {
    val shape = RoundedCornerShape(6.dp)
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = if (filled) Pool.colors.onAccent else color,
        maxLines = 1,
        modifier = Modifier
            .clip(shape)
            .background(if (filled) color else color.copy(alpha = 0.12f))
            .padding(horizontal = 7.dp, vertical = 2.dp),
    )
}

@Composable
fun Dot(color: Color, size: Dp = 9.dp, hollow: Boolean = false) {
    Box(
        Modifier.size(size).clip(CircleShape)
            .then(if (hollow) Modifier.border(1.5.dp, color, CircleShape) else Modifier.background(color)),
    )
}

@Composable
@ReadOnlyComposable
fun phaseColor(phase: Phase): Color = when (phase) {
    Phase.LIVE -> Pool.colors.busy
    Phase.WAITING -> Pool.colors.accentInk
    Phase.GOOD -> Pool.colors.good
    Phase.BAD -> Pool.colors.bad
    Phase.STOPPED -> Pool.colors.muted
}

/** Where one of a computer's measured numbers stands: a thin bar, only drawn when the number is known. */
@Composable
fun Meter(fraction: Float, color: Color, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(4.dp)
    Box(modifier.fillMaxWidth().height(6.dp).clip(shape).background(Pool.colors.sunken).border(1.dp, Pool.colors.line, shape)) {
        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(6.dp).background(color))
    }
}

/** A line of facts: the name on the left, the value on the right; left out when there is no value. */
@Composable
fun Fact(name: String, value: String, mono: Boolean = false) {
    if (value.isBlank()) return
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.Top) {
        Text(name, style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted, modifier = Modifier.width(112.dp))
        Text(value, style = if (mono) Pool.mono else MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
    }
}

@Composable
fun EmptyState(title: String, body: String, modifier: Modifier = Modifier, action: String = "", onAction: () -> Unit = {}) {
    Column(modifier.fillMaxWidth().padding(horizontal = 32.dp, vertical = 48.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(6.dp))
        Text(body, style = MaterialTheme.typography.bodyMedium, color = Pool.colors.muted, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        if (action.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            PrimaryButton(action, onAction)
        }
    }
}

/** A strip across the screen that says something about everything below it. */
@Composable
fun Banner(text: String, color: Color, textColor: Color, action: String = "", onAction: () -> Unit = {}) {
    Row(Modifier.fillMaxWidth().background(color).padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.bodySmall, color = textColor, modifier = Modifier.weight(1f))
        if (action.isNotEmpty()) {
            Text(action, style = MaterialTheme.typography.labelMedium, color = textColor, modifier = Modifier.clickable(onClick = onAction).padding(8.dp))
        }
    }
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, working: Boolean = false, icon: ImageVector? = null) {
    Button(
        onClick = onClick,
        enabled = enabled && !working,
        shape = RoundedCornerShape(10.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Pool.colors.accent, contentColor = Pool.colors.onAccent),
        modifier = modifier.heightIn(min = 46.dp),
    ) {
        if (working) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = Pool.colors.muted)
            Spacer(Modifier.width(8.dp))
        } else if (icon != null) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
        }
        Text(text)
    }
}

@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, working: Boolean = false, danger: Boolean = false) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled && !working,
        shape = RoundedCornerShape(10.dp),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = if (danger) Pool.colors.bad else Pool.colors.text),
        modifier = modifier.heightIn(min = 44.dp),
    ) {
        if (working) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp, color = Pool.colors.muted)
            Spacer(Modifier.width(8.dp))
        }
        Text(text)
    }
}

/** A row of choices of which one is picked, scrolling when the phone is narrow; `inset` is its space at either side. */
@Composable
fun <T> Chips(options: List<T>, selected: T, label: (T) -> String, tint: Color, onSelect: (T) -> Unit, inset: Dp = 16.dp) {
    Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = inset, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        for (option in options) {
            val on = option == selected
            val shape = RoundedCornerShape(8.dp)
            Text(
                label(option),
                style = MaterialTheme.typography.labelLarge,
                color = if (on) Pool.colors.onAccent else Pool.colors.muted,
                modifier = Modifier
                    .clip(shape)
                    .background(if (on) tint else Pool.colors.surface)
                    .border(1.dp, if (on) tint else Pool.colors.line, shape)
                    .clickable { onSelect(option) }
                    .heightIn(min = 36.dp)
                    .padding(horizontal = 12.dp, vertical = 9.dp),
            )
        }
    }
}
