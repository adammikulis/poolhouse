package app.poolhouse.phone;

/**
 * What a notification of a request shows, decided apart from Android so it can be checked: a lock-screen version that names only
 * the agent, a title, the whole text as the expanded body, which button opens the screen, what the buttons say, whether it must stay
 * quiet, and when it goes away. The text is the one the key signs; the notification never shortens or rewrites it.
 */
final class NotificationModel {
    final String publicTitle;
    final String publicText;
    final String title;
    final String collapsed;
    final String body;
    final boolean approveOpensScreen;
    /** A notice that must not interrupt: no sound, no heads-up, no badge. */
    final boolean quiet;
    /** What the buttons say, as {approve, deny, block}; an empty label is a button the request does not offer. */
    final String[] labels;
    final long timeoutMs;

    private NotificationModel(String[] shownAtLock, String[] shown, String body, boolean opens, boolean quiet, String[] labels, long timeoutMs) {
        this.publicTitle = shownAtLock[0];
        this.publicText = shownAtLock[1];
        this.title = shown[0];
        this.collapsed = shown[1];
        this.body = body;
        this.approveOpensScreen = opens;
        this.quiet = quiet;
        this.labels = labels;
        this.timeoutMs = timeoutMs;
    }

    /** The notification for {@code payload} at {@code nowMs} by the node's clock. */
    static NotificationModel of(PushPayload payload, long nowMs) {
        boolean quiet = "quiet".equals(payload.urgency);
        String agent = payload.agent.isEmpty() ? "a request" : payload.agent;
        String[] atLock = quiet ? new String[] {"A quiet notice is waiting", "Open the app to read it."} : new String[] {"Approval waiting from " + agent, "Unlock to read it."};
        long left = Math.max(1000, payload.expiresMs - nowMs);
        if (payload.view == null) {
            return new NotificationModel(atLock, new String[] {payload.title, "Open to read the whole request."}, payload.title + "\nOpen to read the whole request.", true, quiet,
                    new String[] {"Approve", "Deny", ""}, left);
        }
        ApprovalText parts = payload.view.parts();
        String first = parts.flags.isEmpty() ? parts.asks.isEmpty() ? payload.title : parts.asks.get(0) : "Check this one: " + parts.flags.get(0);
        return new NotificationModel(atLock, new String[] {payload.title, first}, payload.view.text, payload.view.opensScreen(), quiet, payload.view.labels(), left);
    }
}
