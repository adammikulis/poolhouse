/** What the app finds when a build carries this module (core/device/PhonePlugin.kt). */
package app.poolhouse.llm

import android.content.Context
import app.poolhouse.core.device.ComputerLink
import app.poolhouse.core.device.PhonePlugin
import app.poolhouse.core.device.PhoneWorker
import app.poolhouse.core.device.PluginEntry
import app.poolhouse.llm.android.LocalModels
import app.poolhouse.llm.android.PoolNode
import app.poolhouse.llm.ui.LocalChatActivity
import app.poolhouse.llm.ui.ModelsActivity

class LlmPlugin : PhonePlugin {
    override fun entries(context: Any): List<PluginEntry> {
        val models = LocalModels.of(context as Context)
        val chosen = models.chosen()
        val without = if (models.left().isEmpty()) "Download a model to chat with no computer" else "Use the model already on this phone"
        return listOf(
            PluginEntry(
                "Model on this phone",
                chosen?.let { "${it.title}, with no computer" } ?: without,
                LocalChatActivity::class.java.name,
            ),
            PluginEntry("Phone models", "Download, choose and remove models", ModelsActivity::class.java.name),
        )
    }

    override fun worker(context: Any, link: ComputerLink): PhoneWorker = PoolNode.worker(context as Context, link)
}
