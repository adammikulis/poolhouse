package app.poolhouse.phone;

import java.util.List;

/**
 * Every step a person takes, first run and routine, in one place: the screens and the notification take their choices from
 * here, and docs/phone-approval.md ("The friction budget") and contracts/phone-friction.json count the same lists. A step added
 * here fails the checks until the budget says why.
 */
final class ApprovalFlow {
    enum Step { RUN_COMMAND, SCAN_CODE, TAP_NOTIFICATION, TAP_APPROVE, FINGERPRINT }

    private ApprovalFlow() { }

    /**
     * Whether the notification's Approve opens the whole request first: any request that is not routine, and any request the node
     * flagged as looking off, however routine its action. {@code level} is the risk the signed text names.
     */
    static boolean opensScreen(String level, boolean flagged) {
        return flagged || !"routine".equals(level);
    }

    /** What a person does to add the first phone. */
    static List<Step> firstRun() {
        return List.of(Step.RUN_COMMAND, Step.SCAN_CODE, Step.FINGERPRINT);
    }

    /** What a person does to approve a request of this risk (flagged or not) from its notification. */
    static List<Step> approving(String level, boolean flagged) {
        return opensScreen(level, flagged) ? List.of(Step.TAP_NOTIFICATION, Step.TAP_APPROVE, Step.FINGERPRINT) : List.of(Step.TAP_APPROVE, Step.FINGERPRINT);
    }
}
