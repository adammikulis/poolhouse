/**
 * Brings model files onto the phone, going on while the person uses something else, one job at a time: downloading a
 * model they asked for, or copying in the models of a folder they opened.
 */
package app.poolhouse.llm.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.IBinder
import android.os.PowerManager
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.model.Catalog
import app.poolhouse.llm.model.CatalogModel
import app.poolhouse.llm.model.DownloadFailed
import app.poolhouse.llm.model.Downloader
import app.poolhouse.llm.model.ModelFileProblem
import app.poolhouse.llm.model.gigabytes
import java.io.IOException
import kotlin.concurrent.thread
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

private const val CHANNEL = "poolhouse-download"
private const val NOTICE = 42
private const val MODEL = "model"
private const val ACCESS = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION

/**
 * The job under way, or how the last one ended: `failure` is its reason, null while it runs or when stopped. `model` is
 * empty for what concerns no one model, and `copying` is a copy from an opened folder.
 */
data class DownloadState(val model: String, val held: Long, val running: Boolean, val failure: String? = null, val copying: Boolean = false)

class DownloadService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Model downloads", NotificationManager.IMPORTANCE_LOW))
        val model = intent?.getStringExtra(MODEL)?.let(Catalog::byId)
        val folder = intent?.data
        startForeground(NOTICE, notice(model?.title ?: "Models", 0, 1), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        val cancel = if (model == null && folder == null) null else claim(model?.id ?: "", folder != null)
        if (cancel == null) {
            if (active == null) stopSelf()
            return START_NOT_STICKY
        }
        thread(name = "model-files") {
            // The processor stays awake while bytes arrive; the lock is given back with the job.
            val awake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "poolhouse:download")
            awake.acquire()
            try {
                if (folder != null) reuse(folder, cancel, manager) else download(model!!, cancel, manager)
            } finally {
                awake.release()
                active = null
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun download(model: CatalogModel, cancel: CancelToken, manager: NotificationManager) {
        val store = LocalModels.of(this).store
        try {
            Downloader(store).fetch(model, cancel, report(model, copying = false, manager))
            current.value = DownloadState(model.id, store.stored(model), false)
        } catch (failure: DownloadFailed) {
            current.value = DownloadState(model.id, store.stored(model), false, failure.message)
        } catch (failure: ModelFileProblem) {
            current.value = DownloadState(model.id, store.stored(model), false, failure.message)
        } catch (failure: IOException) {
            current.value = DownloadState(model.id, store.stored(model), false, "The connection was lost. Try again to continue.")
        }
    }

    /** Copies in every model the opened folder holds that the phone cannot use yet, then gives the folder's access back. */
    private fun reuse(folder: Uri, cancel: CancelToken, manager: NotificationManager) {
        val store = LocalModels.of(this).store
        var last = DownloadState("", 0, false, "That folder holds no model file this app offers.", copying = true)
        try {
            for (model in Catalog.models.filterNot(store::installed)) {
                val file = found(this, folder, model).firstOrNull() ?: continue
                last = DownloadState(model.id, 0, false, copying = true)
                if (!store.adopt(model, file, cancel, report(model, copying = true, manager))) break
                last = DownloadState(model.id, model.bytes, false, copying = true)
            }
        } catch (failure: IOException) {
            last = last.copy(failure = failure.message ?: "The files could not be copied.")
        } finally {
            runCatching { contentResolver.releasePersistableUriPermission(folder, ACCESS) }
            current.value = last
        }
    }

    private fun report(model: CatalogModel, copying: Boolean, manager: NotificationManager): (Long) -> Unit {
        var told = 0L
        return { held ->
            if (held - told >= 4_000_000 || held == model.bytes) {
                told = held
                current.value = DownloadState(model.id, held, true, copying = copying)
                manager.notify(NOTICE, notice(model.title, held, model.bytes, copying))
            }
        }
    }

    private fun notice(title: String, held: Long, all: Long, copying: Boolean = false): Notification = Notification.Builder(this, CHANNEL)
        .setSmallIcon(android.R.drawable.stat_sys_download)
        .setContentTitle("${if (copying) "Copying" else "Downloading"} $title")
        .setContentText("${gigabytes(held)} of ${gigabytes(all)}")
        .setProgress(1000, (held * 1000 / all.coerceAtLeast(1)).toInt(), false)
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .build()

    companion object {
        private val current = MutableStateFlow<DownloadState?>(null)
        val state: StateFlow<DownloadState?> get() = current

        @Volatile private var active: CancelToken? = null

        @Synchronized
        private fun claim(model: String, copying: Boolean): CancelToken? {
            if (active != null) return null
            return CancelToken().also {
                active = it
                current.value = DownloadState(model, 0, true, copying = copying)
            }
        }

        /** Starts downloading the model; the screen asks about a metered network before it calls this. */
        fun start(context: Context, model: String) {
            context.startForegroundService(Intent(context, DownloadService::class.java).putExtra(MODEL, model))
        }

        /** Copies in the models of the folder the person just opened in the system's picker. */
        fun reuse(context: Context, folder: Uri) {
            // The access outlives the screen that was given it, until the copy is done.
            context.contentResolver.takePersistableUriPermission(folder, ACCESS)
            context.startForegroundService(Intent(context, DownloadService::class.java).setData(folder))
        }

        fun stop() {
            active?.cancel("Stopped.")
        }
    }
}
