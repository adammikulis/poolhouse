/** The catalogue's pins, the fit verdicts, and downloads that resume and check what arrived. */
package app.poolhouse.llm.model

import app.poolhouse.llm.engine.CancelToken
import java.io.ByteArrayInputStream
import java.io.File
import java.security.MessageDigest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

private fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

class CatalogTest {
    @Test fun everyEntryIsPinnedToARevisionAndTheBytesOfItsFile() {
        assertEquals(Catalog.models.size, Catalog.models.map { it.id }.toSet().size)
        for (model in Catalog.models) {
            assertTrue(model.id, model.id.matches(Regex("[a-z0-9-]{1,80}")))
            assertTrue(model.id, model.revision.matches(Regex("[0-9a-f]{40}")))
            assertTrue(model.id, model.file.sha256.matches(Regex("[0-9a-f]{64}")))
            assertTrue(model.id, model.file.bytes > 100_000_000)
            assertEquals("$HUB/${model.repository}/resolve/${model.revision}/${model.file.name}", model.url)
            assertTrue(model.id, model.file.name.endsWith(".litertlm"))
            assertTrue(model.id, model.licence.url.startsWith("https://"))
        }
    }

    @Test fun eachModelIsOneFileThatReadsPicturesUpTo32000Tokens() {
        assertEquals(Catalog.models.size, Catalog.models.map { it.repository }.toSet().size)
        assertTrue(Catalog.models.all { it.images && it.contextLimit == 32_000 })
    }
}

class FitTest {
    private val model = Catalog.models.first()
    private val room = PhoneRoom(12_000_000_000, 6_000_000_000, 200_000_000, 50_000_000_000)

    @Test fun aModelWithinTheFreeMemoryFits() {
        assertEquals(Fit.FITS, verdict(model, room, 0).fit)
    }

    @Test fun aModelOverTheFreeMemoryIsTightAndOverTheWholePhoneTooBig() {
        assertEquals(Fit.TIGHT, verdict(model, room.copy(availableMemory = 1_000_000_000), 0).fit)
        val tooBig = verdict(model, room.copy(totalMemory = 2_000_000_000), 0)
        assertEquals(Fit.TOO_BIG, tooBig.fit)
        assertFalse(tooBig.downloadable)
    }

    @Test fun theMemoryMeasuredOnThisPhoneReplacesTheCeiling() {
        val verdict = verdict(model, room, 0, measured = 6_500_000_000)
        assertEquals(Fit.TIGHT, verdict.fit)
        assertTrue(verdict.words, verdict.words.startsWith("Used 6.5 GB on this phone"))
    }

    @Test fun storageCountsOnlyWhatIsStillToArrive() {
        val small = room.copy(freeStorage = 500_000_000)
        assertFalse(verdict(model, small, 0).downloadable)
        assertTrue(verdict(model, small, model.bytes - 400_000_000).downloadable)
    }
}

class DownloadTest {
    @get:Rule val folder = TemporaryFolder()

    private val whole = ByteArray(3_000_000) { (it % 251).toByte() }
    private val model = Catalog.models.first().copy(file = ModelFile("weights.bin", whole.size.toLong(), sha256(whole)))

    /** A host of the file that records the offsets it was asked for; `limit` cuts each body short. */
    private inner class Host(val resumes: Boolean = true, var limit: Int = Int.MAX_VALUE) : Source {
        val asked = mutableListOf<Long>()

        override fun open(url: String, offset: Long): Body {
            asked += offset
            val from = if (resumes) offset.toInt() else 0
            val length = (whole.size - from).coerceAtMost(limit)
            return Body(resumes && offset > 0, ByteArrayInputStream(whole, from, length))
        }
    }

    private fun store() = ModelStore(folder.root)

    @Test fun aWholeDownloadIsVerifiedAndInstalled() {
        val store = store()
        var last = 0L
        assertTrue(Downloader(store, Host()).fetch(model, CancelToken()) { last = it })
        assertEquals(model.bytes, last)
        assertTrue(store.installed(model))
        assertArrayEquals(whole, store.file(model)!!.readBytes())
        assertTrue(store.delete(model))
        assertFalse(store.installed(model))
        assertEquals(0L, store.stored(model))
    }

    @Test fun anInterruptedDownloadContinuesWhereItStopped() {
        val store = store()
        val host = Host(limit = 1_200_000)
        assertThrows(DownloadFailed::class.java) { Downloader(store, host).fetch(model, CancelToken()) {} }
        assertFalse(store.installed(model))
        assertEquals(1_200_000L, store.stored(model))
        host.limit = Int.MAX_VALUE
        assertTrue(Downloader(store, host).fetch(model, CancelToken()) {})
        assertEquals(1_200_000L, host.asked[1])
        assertTrue(store.installed(model))
    }

    @Test fun aHostThatStartsOverReplacesThePartInsteadOfAppending() {
        val store = store()
        assertThrows(DownloadFailed::class.java) { Downloader(store, Host(limit = 900_000)).fetch(model, CancelToken()) {} }
        assertTrue(Downloader(store, Host(resumes = false)).fetch(model, CancelToken()) {})
        assertArrayEquals(whole, store.file(model)!!.readBytes())
    }

    @Test fun bytesThatDoNotMatchTheChecksumAreRemoved() {
        val store = store()
        val wrong = Source { _, _ -> Body(false, ByteArrayInputStream(ByteArray(whole.size))) }
        val failure = assertThrows(DownloadFailed::class.java) { Downloader(store, wrong).fetch(model, CancelToken()) {} }
        assertTrue(failure.message!!, "checksum" in failure.message!!)
        assertEquals(0L, store.stored(model))
        assertFalse(File(store.directory(model), "weights.bin").exists())
    }

    @Test fun stoppingKeepsWhatArrived() {
        val store = store()
        val cancel = CancelToken()
        val finished = Downloader(store, Host()).fetch(model, cancel) { if (it > 0) cancel.cancel("stop") }
        assertFalse(finished)
        assertTrue(store.stored(model) > 0)
        assertFalse(store.installed(model))
    }

    @Test fun aFileWithoutItsCheckIsCheckedAgainAndNotFetched() {
        val store = store()
        val directory = store.directory(model).apply { mkdirs() }
        File(directory, "weights.bin").writeBytes(whole)
        assertFalse(store.installed(model))
        val host = Host()
        assertTrue(Downloader(store, host).fetch(model, CancelToken()) {})
        assertTrue(host.asked.isEmpty())
        assertTrue(store.installed(model))
    }

    @Test fun aDownloadNeverLeavesHttps() {
        assertThrows(DownloadFailed::class.java) { HttpsSource.open("http://huggingface.co/x", 0) }
    }
}
