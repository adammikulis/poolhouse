package app.poolhouse.core.store

import app.poolhouse.core.api.Session

/** One row of the session list: a session and how deep under its lead it sits. */
data class SessionRow(
    val session: Session,
    val depth: Int,
    val unread: Boolean,
    /** Finished agents under this one that are folded away. */
    val foldedEnded: Int = 0,
)

enum class SessionFilter(val label: String) { ALL("All"), ACTIVE("Working"), NEEDS_YOU("Needs you") }

val Session.needsYou: Boolean get() = state == "asking" || waiting > 0
val Session.ended: Boolean get() = state == "ended"
val Session.canSend: Boolean get() = mode != "none"

/** How a send reaches the agent, when it is not at once: the line shown above the send box and on the row. */
fun deliveryLabel(mode: String): String = when (mode) {
    "next_step" -> "Delivered at its next step"
    "via_parent" -> "Delivered through its lead"
    "next_turn" -> "Waits for its next turn"
    "none" -> "Read only"
    else -> ""
}

fun unread(session: Session, seen: Map<String, Double>): Boolean {
    val mark = seen[session.id] ?: return false
    return session.activeAt > mark && session.lastText.isNotEmpty()
}

/**
 * The list of the first screen: each lead with its agents under it, the groups that need the person first and then the most recently
 * active. Finished agents of a group are folded unless the group is in [expanded]; they stay one tap away and readable.
 */
fun sessionRows(
    sessions: List<Session>,
    seen: Map<String, Double>,
    expanded: Set<String> = emptySet(),
    filter: SessionFilter = SessionFilter.ALL,
    query: String = "",
): List<SessionRow> {
    val byId = sessions.associateBy { it.id }
    val children = sessions.filter { it.parent != null && it.parent in byId && it.parent != it.id }.groupBy { it.parent!! }
    val nested = children.values.flatten().mapTo(HashSet()) { it.id }

    if (filter != SessionFilter.ALL || query.isNotBlank()) {
        val words = query.trim().lowercase()
        return sessions.filter { s ->
            when (filter) {
                SessionFilter.ALL -> true
                SessionFilter.ACTIVE -> s.state == "working" || s.state == "asking"
                SessionFilter.NEEDS_YOU -> s.needsYou
            } && (words.isEmpty() || listOf(s.title, s.agent, s.project, s.device, s.model).any { words in it.lowercase() })
        }.sortedByDescending { it.activeAt }.map { SessionRow(it, 0, unread(it, seen)) }
    }

    val visited = HashSet<String>()
    fun subtree(root: Session): List<Session> {
        if (!visited.add(root.id)) return emptyList()
        return listOf(root) + children[root.id].orEmpty().flatMap(::subtree)
    }
    val groups = sessions.filter { it.id !in nested }.map { it to subtree(it) }
    val ordered = groups.sortedWith(
        compareByDescending<Pair<Session, List<Session>>> { (_, all) -> all.any { it.needsYou } }
            .thenByDescending { (_, all) -> all.maxOf { it.activeAt } },
    )

    val out = ArrayList<SessionRow>()
    val shown = HashSet<String>()
    fun emit(session: Session, depth: Int, open: Boolean) {
        if (!shown.add(session.id)) return
        val under = children[session.id].orEmpty()
        // An ended agent with agents still going under it stays, so they keep their place.
        fun alive(s: Session, seenIds: MutableSet<String>): Boolean =
            seenIds.add(s.id) && (!s.ended || s.needsYou || unread(s, seen) || children[s.id].orEmpty().any { alive(it, seenIds) })
        val (kept, folded) = under.partition { open || alive(it, HashSet()) }
        out.add(SessionRow(session, depth, unread(session, seen), folded.size))
        for (child in kept.sortedWith(compareByDescending<Session> { it.needsYou }.thenBy { it.ended }.thenByDescending { it.activeAt })) {
            emit(child, minOf(depth + 1, 3), open)
        }
    }
    for ((root, _) in ordered) emit(root, 0, root.id in expanded)
    return out
}
