package app.poolhouse.phone;

/**
 * What a notification of a request shows, decided apart from Android so it can be checked: a lock-screen version that names only
 * the agent, a title, the whole text as the expanded body, which button opens the screen, and when it goes away. The text is the
 * one the key signs; the notification never shortens or rewrites it.
 */
final class NotificationModel {
    final String publicTitle;
    final String publicText;
    final String title;
    final String collapsed;
    final String body;
    final boolean approveOpensScreen;
    final long timeoutMs;

    private NotificationModel(String publicTitle, String publicText, String title, String collapsed, String body, boolean opens, long timeoutMs) {
        this.publicTitle = publicTitle;
        this.publicText = publicText;
        this.title = title;
        this.collapsed = collapsed;
        this.body = body;
        this.approveOpensScreen = opens;
        this.timeoutMs = timeoutMs;
    }

    /** The notification for {@code payload} at {@code nowMs} by the node's clock. */
    static NotificationModel of(PushPayload payload, long nowMs) {
        String agent = payload.agent.isEmpty() ? "a request" : payload.agent;
        long left = Math.max(1000, payload.expiresMs - nowMs);
        if (payload.view == null) {
            return new NotificationModel("Approval waiting from " + agent, "Unlock to read it.", payload.title, "Open to read the whole request.",
                    payload.title + "\nOpen to read the whole request.", true, left);
        }
        ApprovalText parts = payload.view.parts();
        String first = parts.flags.isEmpty() ? parts.asks.isEmpty() ? payload.title : parts.asks.get(0) : "Check this one: " + parts.flags.get(0);
        return new NotificationModel("Approval waiting from " + agent, "Unlock to read it.", payload.title, first, payload.view.text,
                payload.view.opensScreen(), left);
    }
}
