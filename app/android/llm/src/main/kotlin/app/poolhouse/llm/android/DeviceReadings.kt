/** What Android says about this phone's memory, battery, heat and network. */
package app.poolhouse.llm.android

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.os.BatteryManager
import android.os.PowerManager
import app.poolhouse.llm.model.PhoneRoom
import app.poolhouse.llm.pool.PhoneCondition

fun phoneRoom(context: Context, freeStorage: Long): PhoneRoom {
    val memory = ActivityManager.MemoryInfo()
    context.getSystemService(ActivityManager::class.java).getMemoryInfo(memory)
    return PhoneRoom(memory.totalMem, memory.availMem, memory.threshold, freeStorage)
}

private fun battery(context: Context): Intent? = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))

fun phoneCondition(context: Context): PhoneCondition {
    val power = context.getSystemService(PowerManager::class.java)
    val state = battery(context)
    val level = state?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
    val scale = state?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
    return PhoneCondition(
        charging = (state?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0,
        batteryPercent = if (level >= 0 && scale > 0) level * 100 / scale else 100,
        powerSave = power.isPowerSaveMode,
        thermalStatus = power.currentThermalStatus,
    )
}

/** The battery's temperature in degrees Celsius, or null when Android does not say. */
fun batteryCelsius(context: Context): Double? =
    battery(context)?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)?.takeIf { it != Int.MIN_VALUE }?.let { it / 10.0 }

/** Whether the network in use is one the person may pay for by the byte, or there is none. */
fun metered(context: Context): Boolean = context.getSystemService(ConnectivityManager::class.java).isActiveNetworkMetered
