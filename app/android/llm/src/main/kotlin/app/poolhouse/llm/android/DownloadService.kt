/** Downloads a model the person asked for, going on while they use something else, one at a time. */
package app.poolhouse.llm.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.model.DownloadFailed
import app.poolhouse.llm.model.Downloader
import app.poolhouse.llm.model.gigabytes
import java.io.IOException
import kotlin.concurrent.thread
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

private const val CHANNEL = "poolhouse-download"
private const val NOTICE = 42
private const val MODEL = "model"

/** The download under way, or how the last one ended: `failure` is its reason, null while it runs or when stopped. */
data class DownloadState(val model: String, val held: Long, val running: Boolean, val failure: String? = null)

class DownloadService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Model downloads", NotificationManager.IMPORTANCE_LOW))
        val model = intent?.getStringExtra(MODEL)?.let(Catalog::byId)
        startForeground(NOTICE, notice(model?.title ?: "A model", 0, 1), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        val cancel = claim(model?.id)
        if (model == null || cancel == null) {
            if (active == null) stopSelf()
            return START_NOT_STICKY
        }
        thread(name = "model-download") {
            val models = LocalModels.of(this)
            // The processor stays awake while bytes arrive; the lock is given back with the download.
            val awake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "poolhouse:download")
            awake.acquire()
            var told = 0L
            try {
                Downloader(models.store).fetch(model, cancel) { held ->
                    if (held - told >= 4_000_000 || held == model.bytes) {
                        told = held
                        current.value = DownloadState(model.id, held, true)
                        manager.notify(NOTICE, notice(model.title, held, model.bytes))
                    }
                }
                current.value = DownloadState(model.id, models.store.stored(model), false)
            } catch (failure: DownloadFailed) {
                current.value = DownloadState(model.id, models.store.stored(model), false, failure.message)
            } catch (failure: IOException) {
                current.value = DownloadState(model.id, models.store.stored(model), false, "The connection was lost. Try again to continue.")
            } finally {
                awake.release()
                active = null
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun notice(title: String, held: Long, all: Long): Notification = Notification.Builder(this, CHANNEL)
        .setSmallIcon(android.R.drawable.stat_sys_download)
        .setContentTitle("Downloading $title")
        .setContentText("${gigabytes(held)} of ${gigabytes(all)}")
        .setProgress(1000, (held * 1000 / all.coerceAtLeast(1)).toInt(), false)
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .build()

    companion object {
        private val current = MutableStateFlow<DownloadState?>(null)
        val state: StateFlow<DownloadState?> get() = current

        @Volatile private var active: Pair<String, CancelToken>? = null

        @Synchronized
        private fun claim(model: String?): CancelToken? {
            if (model == null || active != null) return null
            return CancelToken().also {
                active = model to it
                current.value = DownloadState(model, 0, true)
            }
        }

        /** Starts downloading the model; the screen asks about a metered network before it calls this. */
        fun start(context: Context, model: String) {
            context.startForegroundService(Intent(context, DownloadService::class.java).putExtra(MODEL, model))
        }

        fun stop() {
            active?.second?.cancel("Stopped.")
        }
    }
}
