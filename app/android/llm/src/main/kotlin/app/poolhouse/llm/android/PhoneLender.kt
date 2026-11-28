/** What the phone lends the pool: its chosen model, through the same queue the person's own questions use. */
package app.poolhouse.llm.android

import android.content.Context
import app.poolhouse.llm.engine.Caller
import app.poolhouse.llm.engine.CancelToken
import app.poolhouse.llm.engine.GenerationResult
import app.poolhouse.llm.pool.Lender
import app.poolhouse.llm.pool.Offered
import app.poolhouse.llm.pool.PoolRequest
import app.poolhouse.llm.pool.SwitchStore

/** The runtime's name in docs/phone-api.md. */
private const val RUNTIME = "litert-lm"

class PhoneLender(private val models: LocalModels) : Lender {
    override fun offered(): Offered? = models.chosen()?.let { Offered(it.id, it.title, RUNTIME, models.contextFor(it)) }

    override fun answer(request: PoolRequest, cancel: CancelToken, onText: (String) -> Unit): GenerationResult {
        val model = models.chosen() ?: throw IllegalStateException("The phone has no model downloaded.")
        return models.generate(Caller.POOL, model, request.turns, request.options, cancel, onText = onText)
    }
}

/** The switch as the phone keeps it: off until the person turns it on. */
class SwitchPreferences(context: Context) : SwitchStore {
    private val held = context.getSharedPreferences("poolhouse-llm-pool", Context.MODE_PRIVATE)

    override var enabled: Boolean
        get() = held.getBoolean("enabled", false)
        set(value) = held.edit().putBoolean("enabled", value).apply()

    override var unsent: Boolean
        get() = held.getBoolean("unsent", false)
        set(value) = held.edit().putBoolean("unsent", value).apply()
}
