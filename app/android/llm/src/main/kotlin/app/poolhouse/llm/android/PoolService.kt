/** Keeps the app running while the phone is lent, and shows what it is doing with a button that turns it off. */
package app.poolhouse.llm.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import app.poolhouse.core.device.WorkerState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

private const val CHANNEL = "poolhouse-lend"
private const val NOTICE = 41
private const val TURN_OFF = "app.poolhouse.llm.TURN_OFF"

class PoolService : Service() {
    private var watching: CoroutineScope? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val worker = PoolNode.current()
        if (intent?.action == TURN_OFF) {
            worker?.setEnabled(false)
            stopSelf()
            return START_NOT_STICKY
        }
        getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CHANNEL, "Lending this phone", NotificationManager.IMPORTANCE_LOW))
        startForeground(NOTICE, notice(worker?.state?.value), ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        if (worker == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (watching == null) {
            watching = CoroutineScope(SupervisorJob() + Dispatchers.Main).also { scope ->
                scope.launch {
                    worker.state.collect {
                        getSystemService(NotificationManager::class.java).notify(NOTICE, notice(it))
                    }
                }
            }
        }
        return START_NOT_STICKY
    }

    private fun notice(state: WorkerState?): Notification {
        val off = PendingIntent.getService(
            this, 0, Intent(this, PoolService::class.java).setAction(TURN_OFF), PendingIntent.FLAG_IMMUTABLE,
        )
        // The notice names the computer and the state, never anything a request holds.
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("This phone is lent to your pool")
            .setContentText(state?.blocked ?: state?.detail ?: "Starting.")
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Turn off", off).build())
            .build()
    }

    override fun onDestroy() {
        watching?.cancel()
        watching = null
        super.onDestroy()
    }

    companion object {
        fun start(context: Context) {
            // Android lets an app start this only while the person is in it; the worker goes on without it otherwise.
            runCatching { context.startForegroundService(Intent(context, PoolService::class.java)) }
                .onFailure { Log.w("PoolhouseLlm", "the lending notice could not start: ${it.javaClass.simpleName}") }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, PoolService::class.java))
        }
    }
}
