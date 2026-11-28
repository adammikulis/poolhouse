package app.poolhouse.phone.platform

import android.app.ActivityManager
import android.content.Context
import android.os.Build
import android.os.StatFs

/** What Android says about this phone: what the devices screen shows of it, with or without a computer. */
data class PhoneFacts(
    val name: String,
    val android: String,
    val chip: String,
    val cores: Int,
    val memoryUsed: Long,
    val memoryTotal: Long,
    val storageFree: Long,
    val storageTotal: Long,
)

fun phoneFacts(context: Context): PhoneFacts {
    val memory = ActivityManager.MemoryInfo()
    context.getSystemService(ActivityManager::class.java).getMemoryInfo(memory)
    val storage = StatFs(context.filesDir.path)
    return PhoneFacts(
        name = listOf(Build.MANUFACTURER.replaceFirstChar { it.uppercase() }, Build.MODEL).filter { it.isNotBlank() }.joinToString(" "),
        android = "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
        chip = Build.SOC_MODEL.takeIf { it.isNotBlank() && it != Build.UNKNOWN }.orEmpty(),
        cores = Runtime.getRuntime().availableProcessors(),
        memoryUsed = memory.totalMem - memory.availMem,
        memoryTotal = memory.totalMem,
        storageFree = storage.availableBytes,
        storageTotal = storage.totalBytes,
    )
}
