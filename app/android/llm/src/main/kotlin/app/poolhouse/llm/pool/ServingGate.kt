/** When the phone takes the pool's work, from its battery and its heat. */
package app.poolhouse.llm.pool

/** `PowerManager.THERMAL_STATUS_MODERATE`: Android starts holding the phone back. */
const val THERMAL_MODERATE = 2

/** `PowerManager.THERMAL_STATUS_SEVERE`: Android says the person notices the throttling. */
const val THERMAL_SEVERE = 3

/** Off the charger the pool leaves the person the last fifth of the battery. */
const val LEAST_BATTERY_PERCENT = 20

data class PhoneCondition(val charging: Boolean, val batteryPercent: Int, val powerSave: Boolean, val thermalStatus: Int)

/** `accept`: a new request may start. `stop`: a running one ends. `reason` is the sentence for the person and the pool. */
data class Gate(val accept: Boolean, val stop: Boolean, val reason: String?)

fun gate(condition: PhoneCondition): Gate = when {
    condition.thermalStatus >= THERMAL_SEVERE -> Gate(false, true, "The phone is too hot.")
    condition.powerSave -> Gate(false, true, "Battery saver is on.")
    !condition.charging && condition.batteryPercent < LEAST_BATTERY_PERCENT ->
        Gate(false, true, "The battery is under $LEAST_BATTERY_PERCENT% and the phone is not charging.")
    condition.thermalStatus >= THERMAL_MODERATE -> Gate(false, false, "The phone is warm; it waits until it cools.")
    else -> Gate(true, false, null)
}
