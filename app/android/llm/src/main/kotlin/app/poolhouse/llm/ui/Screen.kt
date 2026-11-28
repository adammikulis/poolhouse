/** What the module's screens share. */
package app.poolhouse.llm.ui

import android.content.pm.ApplicationInfo
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import app.poolhouse.theme.PoolhouseScreen
import app.poolhouse.theme.ThemePreference

/** A screen in Poolhouse's colours, hidden from screenshots and recent apps in a release build. */
abstract class ModelScreen : ComponentActivity() {
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) {
            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
        enableEdgeToEdge()
        val choice = ThemePreference.read(this)
        setContent { PoolhouseScreen(choice) { Content() } }
    }

    @Composable
    abstract fun Content()
}
