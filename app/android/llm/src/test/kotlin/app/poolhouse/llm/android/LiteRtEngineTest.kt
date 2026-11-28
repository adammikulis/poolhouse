/** What the runtime is asked to load with. The model itself runs only on a phone; this is the configuration it is given. */
package app.poolhouse.llm.android

import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.LoadSettings
import com.google.ai.edge.litertlm.ActivationDataType
import com.google.ai.edge.litertlm.SpeculativeDecodingMode
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LiteRtEngineTest {
    private val settings = LoadSettings(Accelerator.GPU, DEFAULT_CONTEXT, images = true, cacheDir = File("cache"))

    @Test fun draftingIsAskedForUnlessItCannotBeUsed() {
        val config = LiteRtEngine().config("model.litertlm", settings, Accelerator.GPU, Accelerator.GPU)
        assertEquals(SpeculativeDecodingMode.ON, config.speculativeDecodingConfig?.mode)
        // Each processor is tried with the drafter and then without it, before a slower processor.
        assertEquals(
            listOf("GPU GPU true", "GPU GPU false", "GPU CPU true", "GPU CPU false", "CPU CPU true", "CPU CPU false"),
            attempts(settings).map { "${it.text} ${it.vision} ${it.drafting}" },
        )
    }

    @Test fun theGpuComputesIn32BitsSoThatDigitsAreCopiedExactly() {
        val config = LiteRtEngine().config("model.litertlm", settings, Accelerator.GPU, Accelerator.GPU)
        assertEquals(ActivationDataType.FLOAT32, config.activationDataType)
        assertEquals(DEFAULT_CONTEXT, config.maxNumTokens)
        // The processor keeps the runtime's own precision, in which it copied exactly.
        assertNull(LiteRtEngine().config("model.litertlm", settings, Accelerator.CPU, Accelerator.CPU).activationDataType)
    }
}
