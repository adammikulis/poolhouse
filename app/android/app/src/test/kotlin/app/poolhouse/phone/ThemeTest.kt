package app.poolhouse.phone

import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.test.junit4.v2.createComposeRule
import app.poolhouse.theme.PoolhouseTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** A plain Material card, as the model screens draw, shows its text in the theme's text colour, not the faded one. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class ThemeTest {
    @get:Rule
    val screen = createComposeRule()

    @Test
    fun aCardsTextIsTheThemesTextColourInBothThemes() {
        val found = mutableListOf<Pair<Color, Color>>()
        screen.setContent {
            for (dark in listOf(false, true)) {
                PoolhouseTheme(dark) { found += CardDefaults.cardColors().contentColor to MaterialTheme.colorScheme.onSurface }
            }
        }
        screen.waitForIdle()
        assertEquals(found.map { it.second }, found.map { it.first })
    }
}
