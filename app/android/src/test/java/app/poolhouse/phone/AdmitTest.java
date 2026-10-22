package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.Signature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import java.util.regex.Pattern;

/**
 * A phone added to a device in a pool is listed as an admin key at the approving tap: the terms a code carries, the key's acceptance of
 * them, the act that lists the phone and the second signature that approving it makes. The bytes are checked against the ones the device
 * made (tests/fixtures/phone-admit-vectors.json). Arguments: that file and docs/phone-approval.md.
 */
final class AdmitTest {
    private static final String NODE = "3a05903849a971ff4d44654f2497d8f8a593691f56ca7af1c50c55bd03f5f0ba";
    private static final BigInteger ORDER = new BigInteger("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16);
    private static JSONObject vectors;
    private static int checks;

    interface Throwing { void run() throws Exception; }

    interface Change { void apply(JSONObject target) throws Exception; }

    public static void main(String[] args) throws Exception {
        vectors = new JSONObject(Files.readString(Path.of(args[0])));
        theCode();
        theAcceptance();
        theAct();
        theRefusals();
        theTermsAloneAreWrong();
        theDecision();
        theAddition();
        theSteps(Files.readString(Path.of(args[1])));
        System.out.println("Admit protocol checks: " + checks);
    }

    // -- the code ----------------------------------------------------------------------------------------------------------

    private static JSONObject terms() throws Exception { return new JSONObject(vectors.getJSONObject("terms").toString()); }
    private static String key() { return vectors.getString("key"); }
    private static long now() { return vectors.getLong("now_ms"); }
    private static AddLink linked(JSONObject admit) throws Exception { return AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code().put("admit", admit)), 1100); }

    private static void theCode() throws Exception {
        check(AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code()), 1100).admit == null, "a code with no terms reads as one");
        AddLink.Admit admit = linked(terms()).admit;
        equal(vectors.getJSONObject("terms").getString("pool"), admit.pool);
        equal(vectors.getJSONObject("terms").getString("prev"), admit.prev);
        equal(vectors.getJSONObject("terms").getString("person"), admit.person);
        equal(vectors.getJSONObject("terms").getLong("since"), admit.since);
        equal(2L, linked(terms().put("since", 2)).admit.since);
        equal(9007199254740991L, linked(terms().put("since", 9007199254740991L)).admit.since);
        equal("a:b.c_d-E", linked(terms().put("person", "a:b.c_d-E")).admit.person);
        equal(64, linked(terms().put("person", "x".repeat(64))).admit.person.length());
        for (String field : new String[] {"pool", "prev", "since", "person"}) malformedTerms(t -> t.remove(field));
        malformedTerms(t -> t.put("extra", 1));
        for (String pool : new String[] {"AB".repeat(16), "ab".repeat(15), "ab".repeat(17), "zz".repeat(16), ""}) malformedTerms(t -> t.put("pool", pool));
        for (String prev : new String[] {"CD".repeat(32), "cd".repeat(31), "cd".repeat(33), "zz".repeat(32), ""}) malformedTerms(t -> t.put("prev", prev));
        for (Object other : new Object[] {5, true, JSONObject.NULL, new JSONArray()}) {
            malformedTerms(t -> t.put("pool", other));
            malformedTerms(t -> t.put("prev", other));
            malformedTerms(t -> t.put("person", other));
        }
        for (Object since : new Object[] {1, 0, -1, "2", 2.5, true, 9007199254740992L, Long.MAX_VALUE, JSONObject.NULL, new JSONArray()}) malformedTerms(t -> t.put("since", since));
        for (String person : new String[] {"", "x".repeat(65), "ann smith", "an/n", "café", "ann\n", "ann,bob"}) malformedTerms(t -> t.put("person", person));
        for (String since : new String[] {"2.0", "2.5", "2e0", "\"2\""}) {
            String written = PhoneApprovalTest.code().put("admit", terms()).toString().replace("\"since\":2", "\"since\":" + since);
            check(written.contains("\"since\":" + since), "the code is written with the number " + since);
            refused(() -> AddLink.parse(AddLink.PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(written.getBytes(StandardCharsets.UTF_8)), 1100));
        }
        for (Object admit2 : new Object[] {JSONObject.NULL, "ann", 5, true, new JSONArray(), new JSONObject()}) {
            refused(() -> AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code().put("admit", admit2)), 1100));
        }
        refused(() -> AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code().put("admit", terms()).put("extra", 1)), 1100));
        refused(() -> AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code().put("admit", terms()).put("secret", "short")), 1100));
        refused(() -> AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code().put("admit", terms())), 5000));
    }

    private static void malformedTerms(Change change) throws Exception {
        JSONObject admit = terms();
        change.apply(admit);
        refused(() -> linked(admit));
    }

    // -- what the key accepts ----------------------------------------------------------------------------------------------

    private static void theAcceptance() throws Exception {
        AddLink.Admit admit = linked(terms()).admit;
        byte[] point = Hex.parse(key());
        equal(vectors.getString("acceptance_bytes"), Hex.of(AddRequest.acceptanceBytes(admit, point)));
        check(verifies(point, Hex.parse(vectors.getString("acceptance_bytes")), Hex.parse(vectors.getString("acceptance"))), "the acceptance the device made verifies over those bytes");
        JSONObject other = terms();
        for (Object[] change : new Object[][] {{"person", "bob"}, {"since", 3}, {"pool", "ef".repeat(16)}, {"prev", "ef".repeat(32)}}) {
            other.put((String) change[0], change[1]);
            check(!Arrays.equals(Hex.parse(vectors.getString("acceptance_bytes")), AddRequest.acceptanceBytes(linked(other).admit, point)), "terms that differ in " + change[0] + " are another acceptance");
            other = terms();
        }
        byte[] stranger = new SoftwareAuthorizer().publicPoint();
        check(!Arrays.equals(Hex.parse(vectors.getString("acceptance_bytes")), AddRequest.acceptanceBytes(admit, stranger)), "another key is another acceptance");
        JSONObject sent = AddRequest.withAcceptance(new JSONObject().put("name", "x"), Hex.parse(vectors.getString("acceptance")));
        equal(vectors.getString("acceptance"), sent.getString("acceptance"));
        byte[] high = high(Hex.parse(vectors.getString("acceptance")));
        check(!Arrays.equals(high, Hex.parse(vectors.getString("acceptance"))) && verifies(point, Hex.parse(vectors.getString("acceptance_bytes")), high), "the other s of the same acceptance verifies too");
        equal(vectors.getString("acceptance"), AddRequest.withAcceptance(new JSONObject(), high).getString("acceptance"));
    }

    // -- the act -----------------------------------------------------------------------------------------------------------

    /** The text the device writes for a request to add a phone that is listed: the words of the new key, and that the person signs twice. */
    private static String text() throws Exception {
        return String.join("\n", "POOL: SECURE. Each device needs your code to join.", "RISK: SENSITIVE. The new phone can approve everything this phone can.",
                "WHO: A new phone named replacement.", "ASK: Add the phone replacement (key " + Hex.words(Hex.sha256(Hex.parse(key()))) + ").",
                "SCOPE: That phone is added to this device now and listed as an admin key of ann. It counts as an admin only after a hold of 24 hours. You sign twice: this approval, and the listing.",
                "PICK: After your fingerprint you choose the number the new phone shows on its own screen, out of three. A wrong choice counts as a decline.",
                "CANNOT: Nothing else changes. Check that the key above is the one the new phone shows.", "WHY: A phone asked for this on its own screen.", "DENY: If you deny, nothing changes.",
                "EXPIRES: This request ends at 2026-09-21 14:43 UTC.");
    }

    private static JSONObject action() throws Exception {
        return new JSONObject().put("kind", "phone_add").put("phone", "ph-0123456789ab").put("name", vectors.getString("label")).put("key", key())
                .put("fingerprint", Hex.sha256(Hex.parse(key()))).put("act", new JSONObject(vectors.getJSONObject("act").toString()));
    }

    private static JSONObject act(JSONObject action) throws Exception { return action.getJSONObject("act"); }
    private static JSONObject entry(JSONObject action) throws Exception { return act(action).getJSONObject("body").getJSONObject("entry"); }

    /** The challenge the device writes for a request to add a phone: three numbers to choose among, nothing else in the context. */
    private static JSONObject challenge(JSONObject action, String text, long repeat, long block) throws Exception {
        JSONObject context = new JSONObject().put("said", new JSONObject().put("quote", "").put("origin", "none")).put("verdict", "none").put("taint", "unknown")
                .put("held", new JSONObject().put("act_at_ms", 0).put("cancel", false)).put("prompted", "unknown").put("repeat", repeat)
                .put("pick", new JSONArray().put("12").put("47").put("83")).put("block_ms", block);
        return new JSONObject().put("v", 1).put("op", "approval").put("id", "ap-0123456789abcdef").put("node", NODE).put("board", "pool").put("session", "").put("action", action)
                .put("text", text).put("nonce", "00112233445566778899aabbccddeeff").put("created_ms", 1700000000000L).put("expires_ms", 1790001800000L).put("context", context);
    }

    private static Challenge challenge(JSONObject action) throws Exception { return new Challenge(challenge(action, text(), 0, 0)); }

    private static void theAct() throws Exception {
        AdminAddAct listed = AdminAddAct.of(challenge(action()), now());
        equal(vectors.getString("act_bytes"), Hex.of(listed.bytes()));
        equal(vectors.getString("act_id"), listed.id);
        equal("ann", listed.person);
        check(listed.card.contains("ann") && listed.card.contains(Hex.words(Hex.sha256(Hex.parse(key())))) && listed.card.contains("\"replacement\""), "the card names the person and the words of the key");
        byte[] returned = listed.bytes();
        returned[0] = 0;
        equal(vectors.getString("act_bytes"), Hex.of(listed.bytes()));
    }

    private static void theRefusals() throws Exception {
        JSONObject plainAdd = action();
        plainAdd.remove("act");
        check(AdminAddAct.of(challenge(plainAdd), now()) == null, "a phone added with no listing has no act");
        check(AdminAddAct.of(challenge(action().put("kind", "phone_remove")), now()) == null, "a removal is not a listing");
        check(AdminAddAct.of(new Challenge(new JSONObject()), now()) == null, "a request with no action is not a listing");
        long at = vectors.getJSONObject("act").getLong("at_ms");
        accepted(a -> act(a).put("at_ms", now() + AdminAddAct.SKEW_MS).put("not_after", now() + AdminAddAct.SKEW_MS + 1));
        accepted(a -> act(a).put("not_after", at + AdminAddAct.WINDOW_MS));
        accepted(a -> act(a).put("not_after", now() + 1).put("at_ms", now()));
        changed(a -> act(a).put("at_ms", now() + AdminAddAct.SKEW_MS + 1).put("not_after", now() + AdminAddAct.SKEW_MS + 100));
        changed(a -> act(a).put("not_after", at + AdminAddAct.WINDOW_MS + 1));
        changed(a -> act(a).put("not_after", now()));
        changed(a -> act(a).put("not_after", now() - 1));
        changed(a -> act(a).put("not_after", 1));
        changed(a -> act(a).put("at_ms", now() + 1).put("not_after", now() + 1));
        changed(a -> act(a).put("not_after", Long.MAX_VALUE));
        changed(a -> act(a).put("at_ms", Long.MAX_VALUE));
        changed(a -> act(a).put("at_ms", 0));
        changed(a -> act(a).put("at_ms", "1"));
        changed(a -> act(a).put("at_ms", 1.5));
        changed(a -> act(a).put("not_after", "9"));
        refused(() -> AdminAddAct.of(challenge(action()), 0));
        refused(() -> AdminAddAct.of(challenge(action()), -5));
        refused(() -> AdminAddAct.of(challenge(action()), vectors.getJSONObject("act").getLong("not_after")));
        for (Object version : new Object[] {1, 3, "2", 2.0, JSONObject.NULL}) changed(a -> act(a).put("v", version));
        for (String kind : new String[] {"admin_remove", "admit", "admin_add ", "", "phone_add"}) changed(a -> act(a).put("kind", kind));
        for (Object pool : new Object[] {"ef".repeat(16), "AB".repeat(16), "ab".repeat(15), 5}) changed(a -> act(a).put("pool", pool));
        for (Object prev : new Object[] {"ef".repeat(32), "CD".repeat(32), "cd".repeat(31), 5}) changed(a -> act(a).put("prev", prev));
        for (Object nonce : new Object[] {"0", "AB".repeat(16), "ab".repeat(17), 5}) changed(a -> act(a).put("nonce", nonce));
        for (Object epoch : new Object[] {0, 2, 3, -1, "1", 1.0, JSONObject.NULL}) changed(a -> act(a).put("epoch", epoch));
        for (Object signatures : new Object[] {new JSONArray().put(new JSONObject()), new JSONArray().put("x"), "x", JSONObject.NULL, new JSONObject()}) changed(a -> act(a).put("signatures", signatures));
        for (String field : new String[] {"v", "pool", "epoch", "kind", "body", "at_ms", "not_after", "nonce", "prev", "signatures"}) changed(a -> act(a).remove(field));
        changed(a -> act(a).put("summary", "untrusted"));
        changed(a -> act(a).getJSONObject("body").put("summary", "untrusted"));
        changed(a -> act(a).getJSONObject("body").remove("entry"));
        changed(a -> act(a).put("body", new JSONObject()));
        changed(a -> act(a).getJSONObject("body").put("entry", JSONObject.NULL));
        changed(a -> entry(a).put("note", "untrusted"));
        for (String field : new String[] {"key", "alg", "role", "strength", "person", "since", "acceptance", "label"}) changed(a -> entry(a).remove(field));
        for (String alg : new String[] {"ed25519", "P256", ""}) changed(a -> entry(a).put("alg", alg));
        for (String role : new String[] {"recovery", "Admin", ""}) changed(a -> entry(a).put("role", role));
        for (String strength : new String[] {"weak", "strong ", ""}) changed(a -> entry(a).put("strength", strength));
        for (String person : new String[] {"bob", "", "x".repeat(65), "ann smith", "café", "ann\n"}) changed(a -> entry(a).put("person", person));
        for (Object since : new Object[] {1, 3, "2", 2.0, JSONObject.NULL}) changed(a -> entry(a).put("since", since));
        for (String label : new String[] {"", "other", "x".repeat(41), "café"}) changed(a -> entry(a).put("label", label));
        changed(a -> a.put("name", "other"));
        changed(a -> a.put("name", ""));
        changed(a -> a.put("extra", 1));
        for (String field : new String[] {"phone", "name", "key", "fingerprint"}) changed(a -> a.remove(field));
        for (Object bad : new Object[] {JSONObject.NULL, "x", new JSONArray(), new JSONObject()}) changed(a -> a.put("act", bad));
        String other = Hex.of(new SoftwareAuthorizer().publicPoint());
        changed(a -> a.put("key", other).put("fingerprint", Hex.sha256(Hex.parse(other))));
        changed(a -> a.put("key", other));
        changed(a -> a.put("fingerprint", Hex.sha256("another")));
        changed(a -> a.put("fingerprint", "00".repeat(32)));
        changed(a -> a.put("fingerprint", a.getString("fingerprint").toUpperCase(java.util.Locale.ROOT)));
        changed(a -> a.put("key", key().toUpperCase(java.util.Locale.ROOT)));
        String offCurve = "04" + "00".repeat(32) + "01" + "00".repeat(31);
        changed(a -> a.put("key", offCurve).put("fingerprint", Hex.sha256(Hex.parse(offCurve))));
        changed(a -> entry(a).put("key", other));
        String acceptance = vectors.getString("acceptance");
        changed(a -> entry(a).put("acceptance", acceptance.substring(0, acceptance.length() - 1) + (acceptance.endsWith("0") ? "1" : "0")));
        changed(a -> entry(a).put("acceptance", acceptance.substring(2)));
        changed(a -> entry(a).put("acceptance", acceptance.toUpperCase(java.util.Locale.ROOT)));
        for (String garbage : new String[] {"", "00", "0", "3000", "30" + "00".repeat(9), acceptance + "00"}) changed(a -> entry(a).put("acceptance", garbage));
        changed(a -> entry(a).put("acceptance", Hex.of(high(Hex.parse(acceptance)))));
        SoftwareAuthorizer stranger = new SoftwareAuthorizer();
        byte[][] made = new byte[1][];
        stranger.sign(Hex.parse(vectors.getString("acceptance_bytes")), "x", true, new Authorizer.Result() {
            @Override public void signed(byte[] der) { made[0] = P256Signature.normalize(der); }
            @Override public void failed(String sentence) { throw new AssertionError(sentence); }
        });
        changed(a -> entry(a).put("acceptance", Hex.of(made[0])));
    }

    /** An action that lists a key of its own with a correct acceptance, so that only what a test changes is wrong. */
    private static JSONObject fresh(SoftwareAuthorizer owner, String person, long epoch, long since) throws Exception {
        String point = Hex.of(owner.publicPoint());
        JSONObject target = action();
        target.put("key", point).put("fingerprint", Hex.sha256(owner.publicPoint()));
        act(target).put("epoch", epoch);
        entry(target).put("key", point).put("person", person).put("since", since);
        byte[] terms = AddRequest.acceptanceBytes(point, person, since, act(target).getString("pool"), act(target).getString("prev"));
        byte[][] made = new byte[1][];
        owner.sign(terms, "x", true, new Authorizer.Result() {
            @Override public void signed(byte[] der) { made[0] = P256Signature.normalize(der); }
            @Override public void failed(String sentence) { throw new AssertionError(sentence); }
        });
        entry(target).put("acceptance", Hex.of(made[0]));
        return target;
    }

    private static void theTermsAloneAreWrong() throws Exception {
        SoftwareAuthorizer owner = new SoftwareAuthorizer();
        check(AdminAddAct.of(challenge(fresh(owner, "ann", 1, 2)), now()) != null, "a listing with an acceptance of its own is accepted");
        check(AdminAddAct.of(challenge(fresh(owner, "a:b.c_d-E", 4, 5)), now()) != null, "at another epoch for another person");
        for (String person : new String[] {"ann smith", "x".repeat(65), "", "an/n", "ann\n", "ann,bob"}) refused(() -> AdminAddAct.of(challenge(fresh(owner, person, 1, 2)), now()));
        refused(() -> AdminAddAct.of(challenge(fresh(owner, "ann", 1, 3)), now()));
        refused(() -> AdminAddAct.of(challenge(fresh(owner, "ann", 5, 2)), now()));
        refused(() -> AdminAddAct.of(challenge(fresh(owner, "ann", 0, 1)), now()));
        String other = Hex.of(new SoftwareAuthorizer().publicPoint());
        refused(() -> AdminAddAct.of(challenge(fresh(owner, "ann", 1, 2).put("key", other).put("fingerprint", Hex.sha256(Hex.parse(other)))), now()));
        String offCurve = "04" + "00".repeat(32) + "01" + "00".repeat(31);
        refused(() -> AdminAddAct.of(challenge(fresh(owner, "ann", 1, 2).put("key", offCurve).put("fingerprint", Hex.sha256(Hex.parse(offCurve)))), now()));
        JSONObject unknownClock = fresh(owner, "ann", 1, 2);
        act(unknownClock).put("at_ms", 1).put("not_after", 1000);
        check(AdminAddAct.of(challenge(unknownClock), 1) != null, "the same act is read when the clock says it is current");
        refused(() -> AdminAddAct.of(challenge(unknownClock), 0));
        refused(() -> AdminAddAct.of(challenge(unknownClock), -5));
    }

    private static void changed(Change mutate) throws Exception {
        JSONObject target = action();
        mutate.apply(target);
        refused(() -> AdminAddAct.of(challenge(target), now()));
    }

    private static void accepted(Change mutate) throws Exception {
        JSONObject target = action();
        mutate.apply(target);
        check(AdminAddAct.of(challenge(target), now()) != null, "a listing at the edge of what is accepted");
    }

    // -- the approving phone -----------------------------------------------------------------------------------------------

    /** A key that records each time it is asked, can refuse the n-th ask, and can give the other s of every signature. */
    static final class Script implements Authorizer {
        final SoftwareAuthorizer inner;
        final List<String> events;
        final List<String> titles = new ArrayList<>();
        final List<Boolean> strongs = new ArrayList<>();
        int refuseAt;
        boolean highS;

        Script(List<String> events) throws Exception {
            this.inner = new SoftwareAuthorizer();
            this.events = events;
        }

        @Override public byte[] publicPoint() throws Exception { return inner.publicPoint(); }

        @Override public boolean strongAvailable() { return inner.strongAvailable(); }

        @Override public void sign(byte[] message, String title, boolean strong, Result result) {
            titles.add(title);
            strongs.add(strong);
            events.add("sign:" + title);
            if (refuseAt == titles.size()) {
                result.failed("Nothing was signed: the fingerprint was not recognised.");
                return;
            }
            inner.sign(message, title, strong, new Result() {
                @Override public void signed(byte[] der) { result.signed(highS ? high(der) : der); }
                @Override public void failed(String sentence) { result.failed(sentence); }
            });
        }
    }

    private static final class Outcome {
        String answered;
        String failed;
        final PhoneApprovalTest.Recording node = new PhoneApprovalTest.Recording();
        final List<String> events;

        Outcome(List<String> events) { this.events = events; }

        JSONObject body() { return new JSONObject(node.sent.get(0).substring(node.sent.get(0).indexOf('{'))); }
    }

    private static Outcome decide(Script key, JSONObject challenge, String decision, long nodeNow, String number) throws Exception {
        Outcome outcome = new Outcome(key.events);
        ApprovalView view = PhoneApprovalTest.viewOf(challenge);
        Decider decider = new Decider(new NodeApi(outcome.node, "p".repeat(64), List.of("https://192.168.1.4:7449")), key, NODE);
        decider.decide(view, decision, view.text, nodeNow, (options, chosen, cancelled) -> {
            key.events.add("pick");
            if (number == null) cancelled.run(); else chosen.accept(number);
        }, new Decider.Done() {
            @Override public void answered(String state) { outcome.answered = state; }
            @Override public void failed(String sentence) { outcome.failed = sentence; }
        });
        return outcome;
    }

    private static void theDecision() throws Exception {
        byte[] listing = Hex.parse(vectors.getString("act_bytes"));
        for (boolean highS : new boolean[] {false, true}) {
            Script key = new Script(new ArrayList<>());
            key.highS = highS;
            JSONObject challenge = challenge(action(), text(), 0, 0);
            Outcome sent = decide(key, challenge, "approve", now(), "47");
            equal("approved", sent.answered);
            equal(List.of("sign:Approve", "sign:" + Decider.LISTING_TITLE, "pick"), key.events);
            equal(List.of(true, true), key.strongs);
            JSONObject body = sent.body();
            check(key.inner.verifies(new Challenge(challenge).bytes("approve", Hex.sha256(key.publicPoint())), Hex.parse(body.getString("sig"))), "the approval verifies");
            check(key.inner.verifies(listing, Hex.parse(body.getString("act_sig"))), "the second signature verifies over the bytes of the act");
            check(Arrays.equals(P256Signature.normalize(Hex.parse(body.getString("act_sig"))), Hex.parse(body.getString("act_sig"))), "and is in the one low-s form");
            equal("47", body.getString("pick"));
            equal("approve", body.getString("decision"));
            equal(Hex.sha256(text()), body.getString("text_sha256"));
        }
        Script again = new Script(new ArrayList<>());
        Outcome repeat = decide(again, challenge(action(), text(), 1, 0), "approve_repeat", now(), "12");
        check(repeat.answered != null && again.titles.size() == 2 && repeat.body().has("act_sig"), "a deliberate approval of a scope declined before lists the phone too");
        for (String decision : new String[] {"deny", "block"}) {
            Script key = new Script(new ArrayList<>());
            JSONObject broken = action();
            act(broken).put("epoch", 7);
            for (JSONObject shown : new JSONObject[] {action(), broken}) {
                key.titles.clear();
                Outcome refused = decide(key, challenge(shown, text(), 0, 86400000L), decision, now(), null);
                check(refused.answered != null && key.titles.size() == 1 && !refused.body().has("act_sig") && !refused.body().has("pick"), "a " + decision + " signs once, lists nothing and does not read the act");
                equal(decision, refused.body().getString("decision"));
            }
        }
        Script second = new Script(new ArrayList<>());
        second.refuseAt = 2;
        Outcome cancelled = decide(second, challenge(action(), text(), 0, 0), "approve", now(), "47");
        check(cancelled.answered == null && cancelled.failed.contains("not recognised") && cancelled.node.sent.isEmpty() && second.titles.size() == 2 && !second.events.contains("pick"),
                "leaving the second prompt sends nothing and asks for no number");
        Script first = new Script(new ArrayList<>());
        first.refuseAt = 1;
        Outcome none = decide(first, challenge(action(), text(), 0, 0), "approve", now(), "47");
        check(none.answered == null && none.node.sent.isEmpty() && first.titles.size() == 1, "leaving the first prompt asks for no second");
        Script chooser = new Script(new ArrayList<>());
        Outcome wrong = decide(chooser, challenge(action(), text(), 0, 0), "approve", now(), null);
        check(wrong.answered == null && wrong.node.sent.isEmpty() && wrong.failed.contains("no number was chosen") && chooser.titles.size() == 2, "leaving the question sends neither signature");
        JSONObject moved = action();
        act(moved).put("epoch", 2);
        Script refusing = new Script(new ArrayList<>());
        Outcome unreadable = decide(refusing, challenge(moved, text(), 0, 0), "approve", now(), "47");
        check(unreadable.answered == null && unreadable.failed.contains("listing") && refusing.titles.isEmpty() && unreadable.node.sent.isEmpty(), "an act that does not check asks nobody and sends nothing");
        Script late = new Script(new ArrayList<>());
        Outcome ended = decide(late, challenge(action(), text(), 0, 0), "approve", vectors.getJSONObject("act").getLong("not_after") + 1, "47");
        check(ended.answered == null && late.titles.isEmpty() && ended.node.sent.isEmpty(), "an act that has run out asks nobody");
        JSONObject without = action();
        without.remove("act");
        Script ordinary = new Script(new ArrayList<>());
        Outcome plain = decide(ordinary, challenge(without, text(), 0, 0), "approve", now(), "47");
        check(plain.answered != null && ordinary.titles.size() == 1 && !plain.body().has("act_sig"), "a phone added with no listing is approved with one fingerprint, as before");
        Script weak = new Script(new ArrayList<>());
        weak.inner.strongSetUp = false;
        Outcome pin = decide(weak, challenge(action(), text(), 0, 0), "approve", now(), "47");
        check(pin.answered == null && weak.titles.isEmpty() && pin.failed.contains("strong face unlock"), "a phone with only a PIN is not asked to list a phone");
        Outcome deny = decide(weak, challenge(action(), text(), 0, 0), "deny", now(), null);
        check(deny.answered != null && !weak.strongs.get(0), "but may still deny");
        Script routine = new Script(new ArrayList<>());
        String claimed = text().replace("RISK: SENSITIVE.", "RISK: ROUTINE.");
        Outcome downgraded = decide(routine, challenge(action(), claimed, 0, 0), "approve", now(), "47");
        check(downgraded.answered != null && routine.strongs.equals(List.of(true, true)), "a text that calls the request routine does not lower either prompt");
    }

    // -- the phone being added ---------------------------------------------------------------------------------------------

    private static JSONObject request(AddLink link, Script key) throws Exception {
        return AddRequest.body(link, vectors.getString("label"), key.publicPoint(), new SoftwareAuthorizer().publicPoint(), new byte[16], 1700000000000L);
    }

    private static void theAddition() throws Exception {
        AddLink link = linked(terms());
        for (boolean highS : new boolean[] {false, true}) {
            Script key = new Script(new ArrayList<>());
            key.highS = highS;
            JSONObject body = request(link, key);
            JSONObject[] made = new JSONObject[1];
            String[] why = new String[1];
            AddSigning.sign(key, link, NODE, body, new AddSigning.Done() {
                @Override public void signed(JSONObject request) { made[0] = request; }
                @Override public void failed(String sentence) { why[0] = sentence; }
            });
            check(made[0] != null && why[0] == null, "a phone added under terms signs both");
            equal(List.of(AddSigning.PROOF_TITLE, AddSigning.ACCEPT_TITLE), key.titles);
            equal(List.of(false, true), key.strongs);
            equal(8, made[0].length());
            check(key.inner.verifies(AddRequest.proofBytes(NODE, body), Hex.parse(made[0].getString("proof"))), "the proof is the one the device checks");
            byte[] accepted = Hex.parse(made[0].getString("acceptance"));
            check(key.inner.verifies(AddRequest.acceptanceBytes(link.admit, key.publicPoint()), accepted), "the acceptance verifies over the terms of the code");
            check(Arrays.equals(P256Signature.normalize(accepted), accepted), "in the one low-s form");
            JSONObject listed = action();
            String point = Hex.of(key.publicPoint());
            listed.put("key", point).put("fingerprint", Hex.sha256(key.publicPoint()));
            entry(listed).put("key", point).put("acceptance", made[0].getString("acceptance"));
            check(AdminAddAct.of(challenge(listed), now()) != null, "an act that lists the key with the acceptance this phone made is accepted by the approving phone");
        }
        Script alone = new Script(new ArrayList<>());
        AddLink first = AddLink.parse(PhoneApprovalTest.link(PhoneApprovalTest.code()), 1100);
        alone.inner.strongSetUp = false;
        JSONObject[] one = new JSONObject[1];
        AddSigning.sign(alone, first, NODE, request(first, alone), new AddSigning.Done() {
            @Override public void signed(JSONObject request) { one[0] = request; }
            @Override public void failed(String sentence) { throw new AssertionError(sentence); }
        });
        check(one[0] != null && alone.titles.equals(List.of(AddSigning.PROOF_TITLE)) && one[0].length() == 7 && !one[0].has("acceptance"), "a code with no terms is signed once, as before, even on a phone with only a PIN");
        for (int refuse = 1; refuse <= 2; refuse++) {
            Script script = new Script(new ArrayList<>());
            script.refuseAt = refuse;
            String[] sentence = new String[1];
            AddSigning.sign(script, link, NODE, request(link, script), new AddSigning.Done() {
                @Override public void signed(JSONObject request) { throw new AssertionError("a prompt left sends nothing"); }
                @Override public void failed(String why) { sentence[0] = why; }
            });
            check(sentence[0] != null && script.titles.size() == refuse, "leaving prompt " + refuse + " sends nothing");
        }
        Script stranger = new Script(new ArrayList<>());
        String[] mismatch = new String[1];
        AddSigning.sign(stranger, link, NODE, request(link, new Script(new ArrayList<>())), new AddSigning.Done() {
            @Override public void signed(JSONObject request) { throw new AssertionError("a request for another key is not signed"); }
            @Override public void failed(String sentence) { mismatch[0] = sentence; }
        });
        check(mismatch[0] != null && stranger.titles.isEmpty(), "a request whose key is not this phone's asks nobody");
        Script pin = new Script(new ArrayList<>());
        pin.inner.strongSetUp = false;
        String[] said = new String[1];
        AddSigning.sign(pin, link, NODE, request(link, pin), new AddSigning.Done() {
            @Override public void signed(JSONObject request) { throw new AssertionError("no strong key, no listing"); }
            @Override public void failed(String sentence) { said[0] = sentence; }
        });
        check(said[0] != null && said[0].contains("strong face unlock") && pin.titles.isEmpty(), "a phone with only a PIN is told before any prompt");
    }

    // -- what a person does -------------------------------------------------------------------------------------------------

    private static void theSteps(String doc) {
        List<ApprovalFlow.Step> steps = ApprovalFlow.approvingListing("sensitive", false, false);
        equal(List.of(ApprovalFlow.Step.TAP_NOTIFICATION, ApprovalFlow.Step.TAP_APPROVE, ApprovalFlow.Step.FINGERPRINT, ApprovalFlow.Step.FINGERPRINT, ApprovalFlow.Step.PICK), steps);
        equal(ApprovalFlow.Step.UNLOCK, ApprovalFlow.approvingListing("sensitive", false, true).get(0));
        equal(ApprovalFlow.approving("sensitive", false, true).size() + 1, ApprovalFlow.approvingListing("sensitive", false, true).size());
        equal(4, ApprovalFlow.approving("sensitive", false).size());
        equal(List.of(ApprovalFlow.Step.SCAN_CODE, ApprovalFlow.Step.FINGERPRINT), ApprovalFlow.addingLater(false));
        equal(List.of(ApprovalFlow.Step.SCAN_CODE, ApprovalFlow.Step.FINGERPRINT, ApprovalFlow.Step.FINGERPRINT), ApprovalFlow.addingLater(true));
        int extra = count(steps, ApprovalFlow.Step.FINGERPRINT) - count(ApprovalFlow.approving("sensitive", false), ApprovalFlow.Step.FINGERPRINT)
                + count(ApprovalFlow.addingLater(true), ApprovalFlow.Step.FINGERPRINT) - count(ApprovalFlow.addingLater(false), ApprovalFlow.Step.FINGERPRINT);
        equal(2, extra);
        String heading = "**Another phone, in a pool that lists its admins.**";
        check(doc.contains(heading), "the document lists the steps a listing adds");
        List<String> rows = new ArrayList<>();
        boolean started = false;
        for (String line : doc.substring(doc.indexOf(heading) + heading.length()).split("\n")) {
            if (line.startsWith("|")) {
                started = true;
                if (Pattern.compile("^\\|\\s*\\d+\\s*\\|").matcher(line).find()) rows.add(line);
            } else if (started) {
                break;
            }
        }
        equal(extra, rows.size());
        for (String line : rows) {
            String why = line.substring(line.lastIndexOf('|', line.length() - 2) + 1, line.length() - 1).trim();
            check(!why.isEmpty(), "each added step says why it is needed");
            check(line.toLowerCase(java.util.Locale.ROOT).contains("fingerprint"), "each added step is a fingerprint");
        }
    }

    private static int count(List<ApprovalFlow.Step> steps, ApprovalFlow.Step step) {
        int n = 0;
        for (ApprovalFlow.Step each : steps) if (each == step) n++;
        return n;
    }

    // -- helpers -----------------------------------------------------------------------------------------------------------

    private static boolean verifies(byte[] point, byte[] message, byte[] der) throws Exception {
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initVerify(WebPushCipher.publicKey(point));
        signature.update(message);
        return signature.verify(der);
    }

    /** The same signature with the s that is in the upper half of the group. */
    static byte[] high(byte[] der) {
        int rLength = der[3];
        BigInteger r = new BigInteger(1, Arrays.copyOfRange(der, 4, 4 + rLength));
        int at = 4 + rLength;
        BigInteger s = new BigInteger(1, Arrays.copyOfRange(der, at + 2, at + 2 + der[at + 1]));
        if (s.compareTo(ORDER.shiftRight(1)) <= 0) s = ORDER.subtract(s);
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        for (BigInteger value : new BigInteger[] {r, s}) {
            byte[] bytes = value.toByteArray();
            body.write(2);
            body.write(bytes.length);
            body.write(bytes, 0, bytes.length);
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(0x30);
        out.write(body.size());
        out.write(body.toByteArray(), 0, body.size());
        return out.toByteArray();
    }

    private static void check(boolean condition, String what) {
        checks++;
        if (!condition) throw new AssertionError(what);
    }

    private static void equal(Object expected, Object actual) {
        checks++;
        if (!expected.equals(actual)) throw new AssertionError(expected + " != " + actual);
    }

    private static void refused(Throwing task) throws Exception {
        checks++;
        try {
            task.run();
        } catch (IllegalArgumentException | SecurityException | org.json.JSONException expected) {
            return;
        }
        throw new AssertionError("Accepted a hostile input");
    }
}
