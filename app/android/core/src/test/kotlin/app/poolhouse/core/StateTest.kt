package app.poolhouse.core

import app.poolhouse.core.api.Event
import app.poolhouse.core.api.Job
import app.poolhouse.core.api.PhoneJson
import app.poolhouse.core.api.Question
import app.poolhouse.core.api.Run
import app.poolhouse.core.api.Session
import app.poolhouse.core.store.Delivery
import app.poolhouse.core.store.Draft
import app.poolhouse.core.store.Item
import app.poolhouse.core.store.Phase
import app.poolhouse.core.store.SessionFilter
import app.poolhouse.core.store.Transcript
import app.poolhouse.core.store.delegateRow
import app.poolhouse.core.store.items
import app.poolhouse.core.store.jobRow
import app.poolhouse.core.store.openQuestions
import app.poolhouse.core.store.recordLines
import app.poolhouse.core.store.sessionRows
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StateTest {
    private fun event(id: String, at: Double, type: String, text: String = "", message: String = "", from: String = "", ok: Boolean = true) =
        Event(id = id, at = at, type = type, text = text, message = message, from = from, ok = ok)

    private fun session(id: String, at: Double, parent: String? = null, state: String = "idle", mode: String = "wake") =
        Session(id = id, activeAt = at, parent = parent, state = state, mode = mode, lastText = "text", title = id)

    @Test
    fun anEventIsHeldOnceAndAPageOlderThanTheStreamSortsIn() {
        val streamed = Transcript().add(listOf(event("e3", 3.0, "reply", "third")))
        val all = streamed.page(listOf(event("e1", 1.0, "reply", "first"), event("e2", 2.0, "reply", "second"), event("e3", 3.0, "reply", "third")), "c1")
        assertEquals(listOf("e1", "e2", "e3"), all.events.map { it.id })
        assertEquals("c1", all.next)
        assertTrue(all.loaded)
        assertEquals(3, all.add(all.events).events.size)
    }

    @Test
    fun aMessageIsShownOnceFromTheDraftToTheDelivery() {
        var t = Transcript().withDraft(Draft("local-1", "stop at the tests", 10.0))
        assertEquals(Delivery.SENDING, (items(t).single() as Item.Outgoing).delivery)
        t = t.withDraft(Draft("local-1", "stop at the tests", 10.0, message = "m-1", mode = "next_step", note = "At its next step."))
        assertEquals("At its next step.", (items(t).single() as Item.Outgoing).detail)
        t = t.add(listOf(event("e1", 10.1, "message", "stop at the tests", message = "m-1")))
        assertEquals(Delivery.WAITING, (items(t).single() as Item.Outgoing).delivery)
        t = t.add(listOf(event("e2", 12.0, "delivered", message = "m-1"), event("e3", 12.1, "prompt", "stop at the tests", from = "phone")))
        assertEquals(Delivery.DELIVERED, (items(t).single() as Item.Outgoing).delivery)
    }

    @Test
    fun theHistoryCanShowAMessageBeforeTheSendReturns() {
        val t = Transcript().withDraft(Draft("local-1", "hi", 1.0)).add(listOf(event("e1", 1.0, "message", "hi", message = "m-1")))
            .withDraft(Draft("local-1", "hi", 1.0, message = "m-1"))
        assertEquals(1, items(t).size)
    }

    @Test
    fun aDroppedOrUnsentMessageSaysWhy() {
        val dropped = Transcript().add(listOf(event("e1", 1.0, "message", "hi", message = "m-1"), event("e2", 2.0, "dropped", "It waited a day.", message = "m-1")))
        assertEquals("It waited a day.", (items(dropped).single() as Item.Outgoing).detail)
        val failed = Transcript().withDraft(Draft("local-2", "hi", 1.0, failed = "The computer cannot be reached."))
        assertEquals(Delivery.FAILED, (items(failed).single() as Item.Outgoing).delivery)
    }

    @Test
    fun toolsThatFollowEachOtherAreOneGroup() {
        val t = Transcript().add(listOf(event("1", 1.0, "progress", "read a.py"), event("2", 2.0, "progress", "ran tests", ok = false),
            event("3", 3.0, "reply", "done"), event("4", 4.0, "progress", "wrote b.py")))
        val shown = items(t)
        assertEquals(3, shown.size)
        assertEquals(listOf(true, false), (shown[0] as Item.Steps).steps.map { it.ok })
        assertEquals(1, (shown[2] as Item.Steps).steps.size)
    }

    @Test
    fun aQuestionWaitsUntilItIsAnsweredOrRunsOut() {
        val ask = Event(id = "q", at = 1.0, type = "question", question = "q-1", questions = listOf(Question("Which?")), expiresAt = 100.0)
        val open = items(Transcript().add(listOf(ask)))
        assertEquals(1, openQuestions(open, 50.0).size)
        assertTrue(openQuestions(open, 101.0).isEmpty())
        val answered = items(Transcript().add(listOf(ask, Event(id = "a", at = 2.0, type = "answered", question = "q-1", by = "computer"))))
        assertTrue(openQuestions(answered, 50.0).isEmpty())
        assertEquals("computer", (answered.single() as Item.Ask).answeredBy)
    }

    @Test
    fun agentsSitUnderTheirLeadAndTheOneThatNeedsThePersonComesFirst() {
        val list = listOf(session("quiet", 50.0), session("lead", 10.0), session("child", 20.0, parent = "lead", state = "asking"),
            session("done", 30.0, parent = "lead", state = "ended"), session("orphan", 5.0, parent = "gone"))
        val rows = sessionRows(list, list.associate { it.id to it.activeAt })
        assertEquals(listOf("lead" to 0, "child" to 1, "quiet" to 0, "orphan" to 0), rows.map { it.session.id to it.depth })
        assertEquals(1, rows[0].foldedEnded)
        val opened = sessionRows(list, list.associate { it.id to it.activeAt }, expanded = setOf("lead"))
        assertEquals(listOf("lead", "child", "done", "quiet", "orphan"), opened.map { it.session.id })
    }

    @Test
    fun aFinishedAgentWithNewTextStaysInSightAndIsUnread() {
        val list = listOf(session("lead", 10.0), session("done", 30.0, parent = "lead", state = "ended"))
        val rows = sessionRows(list, mapOf("lead" to 10.0, "done" to 25.0))
        assertEquals(listOf("lead", "done"), rows.map { it.session.id })
        assertTrue(rows[1].unread)
        assertFalse(rows[0].unread)
    }

    @Test
    fun aCircleOfParentsDoesNotHideOrRepeatASession() {
        val list = listOf(session("a", 1.0, parent = "b"), session("b", 2.0, parent = "a"), session("c", 3.0, parent = "c"))
        assertEquals(listOf("c"), sessionRows(list, emptyMap()).map { it.session.id }.filter { it == "c" })
    }

    @Test
    fun theFiltersAndTheSearchListMatchesFlat() {
        val list = listOf(session("lead", 10.0, state = "working"), session("child", 20.0, parent = "lead", state = "asking"), session("other", 30.0))
        assertEquals(listOf("child", "lead"), sessionRows(list, emptyMap(), filter = SessionFilter.ACTIVE).map { it.session.id })
        assertEquals(listOf("child"), sessionRows(list, emptyMap(), filter = SessionFilter.NEEDS_YOU).map { it.session.id })
        assertEquals(listOf("other"), sessionRows(list, emptyMap(), query = "OTH").map { it.session.id })
    }

    @Test
    fun workSaysWhatCanStillBeStopped() {
        assertTrue(delegateRow(Run(name = "d", state = "asking", task = "\n  Fix it \nmore")).cancellable)
        assertEquals("Fix it", delegateRow(Run(name = "d", state = "working", task = "\n  Fix it \nmore")).title)
        assertFalse(delegateRow(Run(name = "d", state = "done")).cancellable)
        assertTrue(jobRow(Job(id = "j", state = "queued")).cancellable)
        assertFalse(jobRow(Job(id = "j", state = "stopped")).cancellable)
        assertEquals(Phase.BAD, jobRow(Job(id = "j", state = "failed")).phase)
    }

    @Test
    fun aDelegatesRecordIsReadWithoutGuessingAtOtherKinds() {
        val events = listOf(
            """{"at":1,"kind":"call","tool":"shell","command":"pytest","denied":false}""",
            """{"at":2,"kind":"result","tool":"shell","status":"completed","exit":1,"output":"1 failed"}""",
            """{"at":3,"kind":"usage","turn":1}""",
            """{"at":4,"kind":"test","passed":3,"failed":0,"ok":true,"command":"pytest"}""",
        ).map { PhoneJson.parseToJsonElement(it).jsonObject }
        val lines = recordLines(events)
        assertEquals(listOf("shell", "shell failed (exit 1)", "Tests: 3 passed, 0 failed"), lines.map { it.title })
        assertEquals("1 failed", lines[1].body)
    }
}
