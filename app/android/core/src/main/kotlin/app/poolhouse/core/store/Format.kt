package app.poolhouse.core.store

import java.util.Locale

/** How long ago `at` (Unix seconds) was, as the rows show it: `now`, `4m`, `3h`, `2d`. Empty when there is no time. */
fun ago(at: Double?, nowSeconds: Double): String {
    if (at == null || at <= 0) return ""
    val seconds = (nowSeconds - at).toLong().coerceAtLeast(0)
    return when {
        seconds < 45 -> "now"
        seconds < 3600 -> "${(seconds + 30) / 60}m"
        seconds < 86_400 -> "${seconds / 3600}h"
        else -> "${seconds / 86_400}d"
    }
}

/** The time until `at`, for a scheduled job or a question that runs out: `in 4m`, or `due`. */
fun until(at: Double?, nowSeconds: Double): String {
    if (at == null || at <= 0) return ""
    val seconds = (at - nowSeconds).toLong()
    return when {
        seconds <= 0 -> "due"
        seconds < 60 -> "in ${seconds}s"
        seconds < 3600 -> "in ${seconds / 60}m"
        seconds < 86_400 -> "in ${seconds / 3600}h ${seconds % 3600 / 60}m"
        else -> "in ${seconds / 86_400}d"
    }
}

fun bytes(count: Long?): String {
    if (count == null || count < 0) return ""
    if (count < 1024) return "$count B"
    var value = count / 1024.0
    var unit = 0
    val units = listOf("KB", "MB", "GB", "TB")
    while (value >= 1024 && unit < units.lastIndex) {
        value /= 1024
        unit++
    }
    return String.format(Locale.ROOT, if (value >= 100) "%.0f %s" else "%.1f %s", value, units[unit])
}

/** A part of a conversation's reply: running text, a heading, a list line or a block of code. */
sealed interface Block {
    data class Paragraph(val text: String) : Block

    data class Heading(val text: String, val level: Int) : Block

    data class Bullet(val text: String, val marker: String, val depth: Int) : Block

    data class Code(val text: String, val language: String) : Block
}

private val HEADING = Regex("^(#{1,4})\\s+(.*)$")
private val BULLET = Regex("^(\\s*)([-*+]|\\d{1,3}[.)])\\s+(.*)$")

/** The Markdown an agent writes, as far as a phone shows it: fenced code, headings, lists and paragraphs. A fence still open holds the rest. */
fun blocks(markdown: String): List<Block> {
    val out = ArrayList<Block>()
    val paragraph = StringBuilder()
    fun flush() {
        if (paragraph.isNotEmpty()) out.add(Block.Paragraph(paragraph.toString()))
        paragraph.setLength(0)
    }
    val lines = markdown.replace("\r\n", "\n").split('\n')
    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        val fence = line.trimStart()
        if (fence.startsWith("```")) {
            flush()
            val language = fence.removePrefix("```").trim()
            val code = ArrayList<String>()
            while (++i < lines.size && !lines[i].trimStart().startsWith("```")) code.add(lines[i])
            out.add(Block.Code(code.joinToString("\n"), language))
        } else if (line.isBlank()) {
            flush()
        } else {
            val heading = HEADING.find(line)
            val bullet = BULLET.find(line)
            when {
                heading != null -> {
                    flush()
                    out.add(Block.Heading(heading.groupValues[2], heading.groupValues[1].length))
                }
                bullet != null -> {
                    flush()
                    val marker = bullet.groupValues[2].let { if (it.length == 1 && it[0] in "-*+") "•" else it }
                    out.add(Block.Bullet(bullet.groupValues[3], marker, (bullet.groupValues[1].length / 2).coerceAtMost(3)))
                }
                else -> {
                    if (paragraph.isNotEmpty()) paragraph.append('\n')
                    paragraph.append(line)
                }
            }
        }
        i++
    }
    flush()
    return out
}

/** A run of a line: plain, `code` or **strong**. */
data class Span(val text: String, val code: Boolean = false, val strong: Boolean = false)

private val INLINE = Regex("`([^`\n]+)`|\\*\\*([^*\n]+)\\*\\*")

fun spans(text: String): List<Span> {
    val out = ArrayList<Span>()
    var from = 0
    for (match in INLINE.findAll(text)) {
        if (match.range.first > from) out.add(Span(text.substring(from, match.range.first)))
        val code = match.groups[1]
        out.add(if (code != null) Span(code.value, code = true) else Span(match.groupValues[2], strong = true))
        from = match.range.last + 1
    }
    if (from < text.length) out.add(Span(text.substring(from)))
    return out
}
