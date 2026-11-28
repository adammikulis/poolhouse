/** What the runtime is asked to load with. The model itself runs only on a phone; this is the configuration it is given. */
package app.poolhouse.llm.android

import app.poolhouse.llm.engine.Accelerator
import app.poolhouse.llm.engine.LoadSettings
import com.google.ai.edge.litertlm.ActivationDataType
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LiteRtEngineTest {
    private val settings = LoadSettings(Accelerator.GPU, DEFAULT_CONTEXT, images = true, cacheDir = File("cache"))

    @Test fun theGpuComputesIn32BitsSoThatDigitsAreCopiedExactly() {
        val config = LiteRtEngine().config("model.litertlm", settings, Accelerator.GPU, Accelerator.GPU)
        assertEquals(ActivationDataType.FLOAT32, config.activationDataType)
        assertEquals(DEFAULT_CONTEXT, config.maxNumTokens)
        // The processor keeps the runtime's own precision, in which it copied exactly.
        assertNull(LiteRtEngine().config("model.litertlm", settings, Accelerator.CPU, Accelerator.CPU).activationDataType)
    }
}
