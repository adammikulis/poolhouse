package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;

/** One request as the node shows it to a phone: its state, the text, and the challenge a key signs. */
final class ApprovalView {
    final JSONObject raw;
    final String id;
    final String state;
    final String title;
    /** The level the signed text names (routine, sensitive or irreversible): never the unsigned field beside it. */
    final String risk;
    final String session;
    final String text;
    /** How loudly the node told the person: urgent, quiet or silent (the list only). */
    final String urgency;
    final long expiresMs;
    final Challenge challenge;

    ApprovalView(JSONObject raw) throws JSONException {
        this.raw = raw;
        id = raw.getString("id");
        state = raw.getString("state");
        title = raw.optString("title", "");
        session = raw.optString("session", "");
        text = raw.getString("text");
        urgency = raw.optString("urgency", "urgent");
        String level = ApprovalText.parse(text).level();
        risk = level.isEmpty() ? "sensitive" : level;
        expiresMs = raw.getLong("expires_ms");
        challenge = new Challenge(raw.getJSONObject("challenge"));
        if (!id.matches("ap-[0-9a-f]{16}") || !challenge.id().equals(id) || !challenge.text().equals(text)) throw new JSONException("The request is not what it says it is.");
    }

    boolean pending() {
        return "pending".equals(state);
    }

    ApprovalText parts() {
        return ApprovalText.parse(text);
    }

    /** Whether the person has to look at the request before anything else: it came to the list, not to the screen, or it is a notice. */
    boolean interrupts() {
        return "urgent".equals(urgency);
    }

    /** Whether Approve opens the whole request before anything is signed: not routine, or flagged by the node. */
    boolean opensScreen() {
        return ApprovalFlow.opensScreen(risk, !parts().flags.isEmpty());
    }

    /** Whether the text has every part its kind carries; nothing is signed for text that leaves one out. */
    boolean whole() {
        return parts().completeFor(challenge.kind());
    }

    /**
     * What the buttons say, as {approve, deny, block}: an empty label is a button the request does not offer. A request an agent made
     * offers approve, deny and (when the text says what it lasts) deny and block; a notice about requests kept back asks something else.
     */
    String[] labels() {
        if ("hold".equals(challenge.kind())) {
            switch (challenge.cause()) {
                case "flood": return new String[] {"Dismiss", "", ""};
                case "suspended": return new String[] {"Lift the suspension", "Keep it suspended", "Block it"};
                default: return new String[] {"Allow once", "Keep holding", "Block it"};
            }
        }
        return new String[] {"Approve", "Deny", challenge.blockMs() > 0 ? "Deny and block" : ""};
    }

    static List<ApprovalView> all(JSONArray list) throws JSONException {
        List<ApprovalView> out = new ArrayList<>();
        for (int i = 0; i < list.length(); i++) out.add(new ApprovalView(list.getJSONObject(i)));
        return out;
    }
}
