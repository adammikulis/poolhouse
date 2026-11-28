package app.poolhouse.phone.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.poolhouse.core.store.Block
import app.poolhouse.core.store.blocks
import app.poolhouse.core.store.spans
import app.poolhouse.phone.ui.theme.Pool

@Composable
private fun inline(text: String): AnnotatedString {
    val code = SpanStyle(fontFamily = FontFamily.Monospace, fontSize = 13.sp, background = Pool.colors.sunken)
    return buildAnnotatedString {
        for (span in spans(text)) {
            when {
                span.code -> withStyle(code) { append(span.text) }
                span.strong -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(span.text) }
                else -> append(span.text)
            }
        }
    }
}

/** An agent's reply: text that can be selected and copied, code that scrolls sideways instead of wrapping. */
@Composable
fun Markdown(text: String, modifier: Modifier = Modifier) {
    val parts = remember(text) { blocks(text) }
    SelectionContainer(modifier) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            for (part in parts) {
                when (part) {
                    is Block.Paragraph -> Text(inline(part.text), style = MaterialTheme.typography.bodyLarge)
                    is Block.Heading -> Text(inline(part.text), style = if (part.level <= 2) MaterialTheme.typography.titleMedium else MaterialTheme.typography.titleSmall)
                    is Block.Bullet -> Row(Modifier.padding(start = (part.depth * 16).dp)) {
                        Text(part.marker, style = MaterialTheme.typography.bodyLarge, color = Pool.colors.muted, modifier = Modifier.widthIn(min = 20.dp).padding(end = 6.dp))
                        Text(inline(part.text), style = MaterialTheme.typography.bodyLarge)
                    }
                    is Block.Code -> CodeBlock(part.text)
                }
            }
        }
    }
}

@Composable
fun CodeBlock(text: String, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(8.dp)
    Column(
        modifier.fillMaxWidth().clip(shape).background(Pool.colors.sunken).border(1.dp, Pool.colors.line, shape)
            .horizontalScroll(rememberScrollState()).padding(10.dp),
    ) {
        Text(text, style = Pool.mono, softWrap = false)
    }
}
