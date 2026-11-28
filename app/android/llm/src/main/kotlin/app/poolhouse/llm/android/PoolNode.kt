/** The phone's one pool worker in this process, what keeps it running while on, and the endpoint for a node on the phone. */
package app.poolhouse.llm.android

import android.content.Context
import app.poolhouse.core.device.ComputerLink
import app.poolhouse.llm.pool.LocalEndpoint
import app.poolhouse.llm.pool.PoolWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

object PoolNode {
    private var worker: PoolWorker? = null

    @Volatile private var link: ComputerLink? = null

    @Volatile var endpoint: LocalEndpoint? = null
        private set

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** The worker, using `connection` for its calls to the computer from now on. */
    @Synchronized
    fun worker(context: Context, connection: ComputerLink): PoolWorker {
        link = connection
        worker?.let { return it }
        val application = context.applicationContext
        val models = LocalModels.of(application)
        val made = PoolWorker(
            ComputerLink { op, body, timeout -> checkNotNull(link) { "No computer is linked." }.call(op, body, timeout) },
            SwitchPreferences(application),
            PhoneLender(models),
            { phoneCondition(application) },
            scope,
            Dispatchers.IO,
            onSwitched = { switched(application, it) },
        )
        worker = made
        if (SwitchPreferences(application).enabled) open(application)
        made.resume()
        return made
    }

    /** The worker once the app has asked for it, or null. */
    @Synchronized
    fun current(): PoolWorker? = worker

    @Synchronized
    private fun switched(context: Context, on: Boolean) {
        if (on) {
            open(context)
        } else {
            // Off listens for nothing: the endpoint closes, and a pool reply on the model ends.
            endpoint?.close()
            endpoint = null
            LocalModels.of(context).host.stopPool("Pool use was turned off on the phone.")
            PoolService.stop(context)
        }
        LocalModelProvider.changed(context)
    }

    /** The endpoint while the switch is on, listening from the first time it is asked for; null while off. */
    @Synchronized
    fun listening(context: Context): LocalEndpoint? {
        val application = context.applicationContext
        if (endpoint == null && SwitchPreferences(application).enabled) {
            endpoint = LocalEndpoint(PhoneLender(LocalModels.of(application))) { phoneCondition(application) }
        }
        return endpoint
    }

    private fun open(context: Context) {
        listening(context)
        PoolService.start(context)
        LocalModelProvider.changed(context)
    }
}
