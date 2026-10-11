package app.poolhouse.phone;

import java.util.ArrayList;
import java.util.List;

/**
 * Every step a person takes, first run and routine, in one place: the screens and the notification take their choices from
 * here, and docs/phone-approval.md ("The friction budget") and contracts/phone-friction.json count the same lists. A step added
 * here fails the checks until the budget says why.
 */
final class ApprovalFlow {
    enum Step { RUN_COMMAND, SCAN_CODE, UNLOCK, TAP_NOTIFICATION, TAP_APPROVE, FINGERPRINT, PICK }

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

    /**
     * What a person does to approve a request of this risk (flagged or not) from its notification: the screen first when it is not routine
     * or was flagged, one fingerprint, and, for what is not routine, one pick of the number the agent showed (so no reflex approves it). A
     * routine request pays for none of the defences against being worn down.
     */
    static List<Step> approving(String level, boolean flagged) {
        return approving(level, flagged, false);
    }

    /**
     * The same for a phone that is {@code locked}: Android does not run an action on a notification until the phone is unlocked, so the
     * unlock is a step of its own, before the rest.
     */
    static List<Step> approving(String level, boolean flagged, boolean locked) {
        List<Step> steps = new ArrayList<>();
        if (locked) steps.add(Step.UNLOCK);
        if (opensScreen(level, flagged)) steps.add(Step.TAP_NOTIFICATION);
        steps.add(Step.TAP_APPROVE);
        steps.add(Step.FINGERPRINT);
        if (!"routine".equals(level)) steps.add(Step.PICK);
        return steps;
    }
}
