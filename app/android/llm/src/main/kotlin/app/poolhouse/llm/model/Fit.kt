/** Whether a model fits this phone, from what Android reports about memory and storage. */
package app.poolhouse.llm.model

/** Android's own reading: `ActivityManager.MemoryInfo` and the free space where models are kept. */
data class PhoneRoom(
    val totalMemory: Long,
    val availableMemory: Long,
    /** The free memory under which Android starts ending processes. */
    val lowMemoryThreshold: Long,
    val freeStorage: Long,
)

enum class Fit { FITS, TIGHT, TOO_BIG, UNKNOWN }

data class Verdict(val fit: Fit, val memoryNeeded: Long, val storageNeeded: Long, val words: String) {
    val downloadable: Boolean get() = fit != Fit.TOO_BIG && storageNeeded >= 0
}

/**
 * The verdict for `model`. `measured` is the most memory the app held with this model loaded on this phone, when it has
 * been loaded here; before that the need is the size of all the files, the most a runtime that holds them can take.
 */
fun verdict(model: CatalogModel, room: PhoneRoom, stored: Long, measured: Long? = null): Verdict {
    val need = measured ?: model.bytes
    val remaining = (model.bytes - stored).coerceAtLeast(0)
    val asMuch = if (measured != null) "Used ${gigabytes(need)} on this phone" else "Needs up to ${gigabytes(need)} of memory"
    if (remaining > room.freeStorage) {
        return Verdict(Fit.TOO_BIG, need, -1, "Needs ${gigabytes(remaining)} of storage; ${gigabytes(room.freeStorage)} is free.")
    }
    if (room.totalMemory <= 0) {
        return Verdict(Fit.UNKNOWN, need, remaining, "$asMuch. This phone did not say how much it has.")
    }
    return when {
        need > room.totalMemory - room.lowMemoryThreshold ->
            Verdict(Fit.TOO_BIG, need, remaining, "$asMuch; this phone has ${gigabytes(room.totalMemory)} in all.")
        need > room.availableMemory - room.lowMemoryThreshold ->
            Verdict(Fit.TIGHT, need, remaining, "$asMuch; ${gigabytes(room.availableMemory)} is free now. Close other apps first.")
        else -> Verdict(Fit.FITS, need, remaining, "$asMuch of the ${gigabytes(room.availableMemory)} free now.")
    }
}

/** Decimal gigabytes, as Android's storage screen counts them. */
fun gigabytes(bytes: Long): String =
    if (bytes < 100_000_000) "${(bytes + 500_000) / 1_000_000} MB" else "%.1f GB".format(java.util.Locale.ROOT, bytes / 1e9)
