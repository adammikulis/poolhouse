package app.poolhouse.phone

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import app.poolhouse.phone.ui.AppRoot

class MainActivity : ComponentActivity() {
    private val model: PhoneViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // A rotation hands the same intent again: a link is acted on only the first time.
        if (savedInstanceState == null) link(intent)
        setContent { AppRoot(model) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        link(intent)
    }

    private fun link(intent: Intent) {
        val data = intent.data ?: return
        if (data.scheme == "poolhouse" && data.host == "link") model.opened(data.toString())
    }

    override fun onStart() {
        super.onStart()
        model.visible(true)
    }

    override fun onStop() {
        model.visible(false)
        super.onStop()
    }
}
