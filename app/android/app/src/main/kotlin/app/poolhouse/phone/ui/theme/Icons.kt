package app.poolhouse.phone.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

/**
 * The app's icons: 24 by 24 line drawings in the web rail's style (1.7 stroke, round ends). The navigation ones are the web rail's own
 * paths (`web(...)`, checked by tests/test_android_theme.py); the rest are drawn here for the phone's controls.
 */
object Icons {
    val Chat by lazy { web("chat", "M4,5h16v11H9l-5,4V5", "M8,9h8M8,12h5") }
    val Work by lazy { web("work", "M7,5h10v3H7V5", "M7,6H4v15h16V6h-3M8,12h8M8,16h5") }
    val Pool by lazy { web("pool", "M3,4h7v6H3zM14,14h7v6h-7z", "M7,10v7h7M10,7h7v7") }
    val Settings by lazy { web("settings", "M9,3h6l1,3,3,1,2,5-2,5-3,1-1,3H9l-1-3-3-1-2-5,2-5,3-1,1-3", "M9,12a3,3,0,1,0,6,0,3,3,0,1,0-6,0") }
    val Models by lazy { web("models", "m12,3,9,5v8l-9,5-9-5V8z", "m3,8,9,5,9-5M12,13v8") }
    val Jobs by lazy { web("jobs", "M4,5h16v14H4z", "m9,9,5,3-5,3V9") }
    val SignOut by lazy { web("exit", "M10,4H4v16h6", "M9,12h12m-5-5,5,5-5,5") }

    val Back by lazy { line("back", "M15,5l-7,7,7,7") }
    val Forward by lazy { line("forward", "M9,5l7,7-7,7") }
    val Down by lazy { line("down", "M6,9l6,6,6-6") }
    val Send by lazy { line("send", "M12,19V5M6,11l6-6,6,6") }
    val Stop by lazy { line("stop", "M8,8h8v8H8z") }
    val Plus by lazy { line("plus", "M12,5v14M5,12h14") }
    val Close by lazy { line("close", "M6,6l12,12M18,6 6,18") }
    val Check by lazy { line("check", "M5,12l5,5 9-11") }
    val Bell by lazy { line("bell", "M6,16V11a6,6,0,0,1,12,0v5l2,2H4z", "M10,21h4") }
    val Search by lazy { line("search", "M11,5a6,6,0,1,0,0,12,6,6,0,1,0,0-12", "M15.5,15.5 20,20") }
    val Scan by lazy { line("scan", "M4,8V4h4M16,4h4v4M20,16v4h-4M8,20H4v-4", "M8,12h8") }
    val Alert by lazy { line("alert", "M12,4 2.5,20h19z", "M12,10v4M12,17v.5") }
    val Phone by lazy { line("phone", "M8,3h8a1,1,0,0,1,1,1v16a1,1,0,0,1-1,1H8a1,1,0,0,1-1-1V4a1,1,0,0,1,1-1z", "M11,18h2") }
    val Clock by lazy { line("clock", "M12,4a8,8,0,1,0,0,16,8,8,0,1,0,0-16", "M12,8v4l3,2") }
    val Tool by lazy { line("tool", "M8,8l-4,4,4,4M16,8l4,4-4,4M14,5l-4,14") }

    private fun web(name: String, vararg paths: String): ImageVector = line(name, *paths)

    private fun line(name: String, vararg paths: String): ImageVector =
        ImageVector.Builder(name = name, defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f)
            .apply {
                for (data in paths) {
                    addPath(
                        pathData = PathParser().parsePathString(data).toNodes(),
                        stroke = SolidColor(Color.Black),
                        strokeLineWidth = 1.7f,
                        strokeLineCap = StrokeCap.Round,
                        strokeLineJoin = StrokeJoin.Round,
                    )
                }
            }
            .build()
}
