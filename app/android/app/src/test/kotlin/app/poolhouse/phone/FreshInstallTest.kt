package app.poolhouse.phone

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isSelectable
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import app.poolhouse.core.device.PLUGIN_CLASS
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * A phone that has never been linked opens on its own screens: no code is asked for, the conversations and this phone on the
 * devices screen are there, the screens that need a computer say so, and linking is offered, never required.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36], qualifiers = "w411dp-h915dp-420dpi")
class FreshInstallTest {
    @get:Rule
    val app = createAndroidComposeRule<MainActivity>()

    private fun tab(name: String) = app.onNode(hasText(name) and isSelectable())

    private fun shown(text: String) = app.onAllNodesWithText(text, substring = true).onFirst().assertIsDisplayed()

    private fun absent(text: String) = app.onAllNodesWithText(text, substring = true).assertCountEquals(0)

    @Test
    fun aFreshInstallReachesChatsAndDevicesWithNoCode() {
        tab("Chats").assertIsDisplayed()
        absent("Scan the code")
        shown("Link a computer")
        val model = runCatching { Class.forName(PLUGIN_CLASS) }.isSuccess
        if (model) shown("Model on this phone") else shown("This build has no model for the phone")

        tab("Pool").performClick()
        shown("This phone")
        shown("Android")
        shown("Link a computer")
        absent("Scan the code")

        tab("Work").performClick()
        shown("needs a computer")
        tab("Inbox").performClick()
        shown("needs a computer")
        absent("Scan the code")
    }

    @Test
    fun linkingIsOfferedAndLeavingItReturnsToTheApp() {
        app.onAllNodesWithText("Link a computer").onFirst().performScrollTo().performClick()
        shown("Scan the code")
        app.activityRule.scenario.onActivity { it.onBackPressedDispatcher.onBackPressed() }
        app.waitForIdle()
        tab("Chats").assertIsDisplayed()
        absent("Scan the code")
    }
}
