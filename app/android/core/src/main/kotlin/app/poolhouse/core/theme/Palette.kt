package app.poolhouse.core.theme

/*
 * The product's colour tokens as 0xAARRGGBB, named after the CSS variables of the web UI
 * (src/poolhouse/fleet/web/foundation.css and navigation.css). tests/test_android_app.py reads both files and fails when a value here
 * is not the web UI's, so a change of the theme on the web is a change here in the same commit.
 */

/** The fixed brand colours, the same in light and dark. */
object Brand {
    const val PINK = 0xFFFF5FA2
    const val ORANGE = 0xFFFF8A3D
    const val YELLOW = 0xFFFFD166
    const val CYAN = 0xFF2DE2E6
    const val NAVY = 0xFF1B1F3A
    const val NAVY_INK = 0xFFFFFFFF

    /** The idle text of the navigation rail, which is navy in both themes. */
    const val RAIL_TEXT = 0xFFC5CADC
}

data class Palette(
    val bg: Long,
    val surface: Long,
    val sunken: Long,
    val line: Long,
    val lineStrong: Long,
    val text: Long,
    val muted: Long,
    val accent: Long,
    val accentInk: Long,
    val accentDim: Long,
    val onAccent: Long,
    val green: Long,
    val yellow: Long,
    val red: Long,
    val warn: Long,
    val busy: Long,
)

object Palettes {
    val light = Palette(
        bg = 0xFFF4F5F7,
        surface = 0xFFFFFFFF,
        sunken = 0xFFEAEDF2,
        line = 0xFFE0E3E9,
        lineStrong = 0xFFB9B6C1,
        text = 0xFF1B1F3A,
        muted = 0xFF616779,
        accent = Brand.PINK,
        // color-mix(in srgb, pink 55%, #000)
        accentInk = 0xFF8C3459,
        accentDim = 0xFFFFE4EF,
        onAccent = Brand.NAVY,
        green = 0xFF16836A,
        yellow = 0xFFFFD166,
        red = 0xFFBD315C,
        warn = 0xFF9B5A00,
        busy = 0xFFBC7200,
    )

    val dark = Palette(
        bg = 0xFF15171E,
        surface = 0xFF20232D,
        sunken = 0xFF191C25,
        line = 0xFF353945,
        lineStrong = 0xFF686F82,
        text = 0xFFEDF0F7,
        muted = 0xFFA2A6B6,
        accent = Brand.PINK,
        accentInk = Brand.PINK,
        accentDim = 0xFF472B3E,
        onAccent = Brand.NAVY,
        green = 0xFF57C8AC,
        yellow = 0xFFFFD166,
        red = 0xFFFF5FA2,
        warn = 0xFF9B5A00,
        busy = 0xFFBC7200,
    )
}

/** The workspace a screen belongs to, each with the colour the web rail gives it. */
enum class Section(val color: Long) {
    CONVERSATIONS(Brand.PINK),
    WORK(Brand.YELLOW),
    POOL(Brand.CYAN),
    MODELS(Brand.ORANGE),
}
