package app.poolhouse.phone;

import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.SecureRandom;
import java.security.Signature;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

/**
 * The approval protocol of the phone, checked without a device: the bytes a key signs against the ones another implementation
 * made, what a request is checked for before a fingerprint is asked, what a notification shows, and what a push reveals.
 * Arguments: the vectors file (tests/fixtures/phone-approval-vectors.json) and the friction contract (contracts/phone-friction.json).
 */
public final class PhoneApprovalTest {
    interface Check { void run() throws Exception; }

    static void check(boolean condition, String what) {
        if (!condition) throw new AssertionError(what);
    }

    static void equal(Object expected, Object actual, String what) {
        if (!expected.equals(actual)) throw new AssertionError(what + ": expected " + expected + " but was " + actual);
    }

    static void refuses(Check check, String what) throws Exception {
        try { check.run(); } catch (IllegalArgumentException | SecurityException | NodeError | org.json.JSONException expected) { return; }
        throw new AssertionError(what + " was accepted");
    }

    static JSONObject read(String path) throws Exception {
        return new JSONObject(new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8));
    }

    static String rfcPlaintext;

    public static void main(String[] args) throws Exception {
        JSONObject vectors = read(args[0]);
        JSONObject contract = read(args[1]);
        canonical();
        theBytesAnotherImplementationMade(vectors);
        whatARequestIsCheckedFor(vectors);
        thePhoneAddRequest(vectors);
        theCode();
        thePushCipher(vectors);
        theText(vectors);
        theNotification(vectors);
        theDecision(vectors);
        theStepsAgainstBeingWornDown(vectors);
        theStrengthOfTheAnswer(vectors);
        theNoticesAndTheContext(vectors);
        thePushHandler(vectors);
        theNodeApi();
        theFrictionBudget(contract);
        System.out.println("Phone approval checks passed.");
    }

    static void canonical() throws Exception {
        JSONObject value = new JSONObject().put("b", 1).put("a", new JSONArray().put("x\ny").put(true).put(new JSONObject().put("z", 2).put("y", 3L)))
                .put("c", "q\"r\\s\t\u0001");
        equal("{\"a\":[\"x\\ny\",true,{\"y\":3,\"z\":2}],\"b\":1,\"c\":\"q\\\"r\\\\s\\t\\u0001\"}", Canonical.of(value), "keys sorted, text escaped as the node escapes it");
        refuses(() -> Canonical.of(new JSONObject().put("a", 1.5)), "a fraction");
        refuses(() -> Canonical.of(new JSONObject().put("a", "café")), "text that is not plain ASCII");
    }

    static void theBytesAnotherImplementationMade(JSONObject vectors) throws Exception {
        Challenge challenge = new Challenge(vectors.getJSONObject("challenge"));
        String key = vectors.getJSONObject("key").getString("fingerprint");
        equal(vectors.getString("approve_bytes"), new String(challenge.bytes("approve", key), StandardCharsets.UTF_8), "the bytes of an approval");
        equal(vectors.getString("deny_bytes"), new String(challenge.bytes("deny", key), StandardCharsets.UTF_8), "the bytes of a deny");
        byte[] point = Hex.parse(vectors.getJSONObject("key").getString("public"));
        equal(key, Hex.sha256(point), "a key is known by the SHA-256 of its point");
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initVerify(WebPushCipher.publicKey(point));
        signature.update(vectors.getString("approve_bytes").getBytes(StandardCharsets.UTF_8));
        check(signature.verify(Hex.parse(vectors.getString("approve_signature"))), "the signature another implementation made verifies here");
        signature.initVerify(WebPushCipher.publicKey(point));
        signature.update(vectors.getString("deny_bytes").getBytes(StandardCharsets.UTF_8));
        check(!signature.verify(Hex.parse(vectors.getString("approve_signature"))), "an approval is not a deny");
        refuses(() -> challenge.bytes("maybe", key), "a decision that is neither");
    }

    static void whatARequestIsCheckedFor(JSONObject vectors) throws Exception {
        JSONObject raw = vectors.getJSONObject("challenge");
        Challenge challenge = new Challenge(raw);
        String node = raw.getString("node");
        String text = raw.getString("text");
        long now = raw.getLong("created_ms");
        challenge.check(node, text, now);
        refuses(() -> challenge.check(Hex.sha256("another"), text, now), "a request for another device");
        refuses(() -> challenge.check(node, text + "\nWHY: nothing", now), "text that is not the text of the request");
        refuses(() -> challenge.check(node, text, raw.getLong("expires_ms")), "a request that has ended");
        refuses(() -> new Challenge(new JSONObject(raw.toString()).put("nonce", "short")).check(node, text, now), "a malformed nonce");
        refuses(() -> new Challenge(new JSONObject(raw.toString()).put("op", "grant")).check(node, text, now), "another kind of document");
    }

    static void thePhoneAddRequest(JSONObject vectors) throws Exception {
        JSONObject add = vectors.getJSONObject("add");
        JSONObject body = add.getJSONObject("body");
        byte[] proof = AddRequest.proofBytes(add.getString("node"), body);
        equal(add.getString("proof_bytes"), new String(proof, StandardCharsets.UTF_8), "the proof of a phone's own addition");
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initVerify(WebPushCipher.publicKey(Hex.parse(body.getString("key"))));
        signature.update(proof);
        check(signature.verify(Hex.parse(add.getString("proof_signature"))), "the proof verifies");
        JSONObject sent = AddRequest.withProof(body, new byte[] {1, 2});
        equal("0102", sent.getString("proof"), "the proof travels in hex");
        equal(7, sent.length(), "the request carries the secret, name, key, push key, push secret, time and proof, and nothing else");
    }

    static String link(JSONObject data) {
        return AddLink.PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(data.toString().getBytes(StandardCharsets.UTF_8));
    }

    static JSONObject code() throws Exception {
        return new JSONObject().put("v", 1).put("pin", "a".repeat(64)).put("endpoints", new JSONArray().put("https://192.168.1.4:7449").put("https://100.64.1.2:7449"))
                .put("secret", "b".repeat(43)).put("expires", 1600).put("name", "studio").put("policy", "secure");
    }

    static void theCode() throws Exception {
        AddLink good = AddLink.parse(link(code()), 1100);
        equal(2, good.endpoints.size(), "the addresses of the code");
        equal("secure", good.policy, "the policy the code names");
        refuses(() -> AddLink.parse(link(code().put("extra", 1)), 1100), "a field the code does not have");
        refuses(() -> AddLink.parse(link(code().put("endpoints", new JSONArray().put("http://192.168.1.4:7449"))), 1100), "a plain HTTP address");
        refuses(() -> AddLink.parse(link(code().put("endpoints", new JSONArray().put("https://user@192.168.1.4:7449"))), 1100), "an address with a user");
        refuses(() -> AddLink.parse(link(code().put("endpoints", new JSONArray())), 1100), "no address");
        refuses(() -> AddLink.parse(link(code().put("endpoints", new JSONArray().put("https://a:1").put("https://b:1").put("https://c:1").put("https://d:1").put("https://e:1"))), 1100), "five addresses");
        refuses(() -> AddLink.parse(link(code().put("secret", "short")), 1100), "a short secret");
        refuses(() -> AddLink.parse(link(code().put("pin", "z".repeat(64))), 1100), "a pin that is not hex");
        refuses(() -> AddLink.parse(link(code().put("policy", "anything")), 1100), "a policy that is neither");
        refuses(() -> AddLink.parse(link(code().put("name", "café")), 1100), "a name that is not plain");
        refuses(() -> AddLink.parse(link(code()), 5000), "a code that ended");
        refuses(() -> AddLink.parse(link(code().put("expires", 99999)), 1100), "a code that lasts longer than ten minutes");
        refuses(() -> AddLink.parse("poolhouse://enroll?data=abc", 1100), "the other kind of link");
        refuses(() -> AddLink.parse(link(code()) + "=", 1100), "padding the node does not write");
    }

    static void thePushCipher(JSONObject vectors) throws Exception {
        JSONObject push = vectors.getJSONObject("push");
        byte[] plain = WebPushCipher.decrypt(Hex.parse(push.getString("body")), WebPushCipher.privateKey(Hex.parse(push.getString("ua_private"))),
                Hex.parse(push.getString("ua_public")), Hex.parse(push.getString("auth")));
        equal(push.getString("plaintext"), new String(plain, StandardCharsets.UTF_8), "the example of RFC 8291 opens");
        byte[] point = Hex.parse(push.getString("ua_public"));
        byte[] auth = Hex.parse(push.getString("auth"));
        byte[] sealed = WebPushCipher.encrypt("approval waiting".getBytes(StandardCharsets.UTF_8), point, auth, new SecureRandom());
        equal("approval waiting", new String(WebPushCipher.decrypt(sealed, WebPushCipher.privateKey(Hex.parse(push.getString("ua_private"))), point, auth), StandardCharsets.UTF_8), "a body made here opens here");
        check(indexOf(sealed, "approval".getBytes(StandardCharsets.UTF_8)) < 0, "the push service reads nothing of the text");
        sealed[sealed.length - 1] ^= 1;
        refuses(() -> WebPushCipher.decrypt(sealed, WebPushCipher.privateKey(Hex.parse(push.getString("ua_private"))), point, auth), "a changed byte");
        byte[] wrong = auth.clone();
        wrong[0] ^= 1;
        byte[] again = WebPushCipher.encrypt(new byte[] {1}, point, auth, new SecureRandom());
        refuses(() -> WebPushCipher.decrypt(again, WebPushCipher.privateKey(Hex.parse(push.getString("ua_private"))), point, wrong), "another secret");
    }

    static int indexOf(byte[] haystack, byte[] needle) {
        outer:
        for (int i = 0; i + needle.length <= haystack.length; i++) {
            for (int j = 0; j < needle.length; j++) if (haystack[i + j] != needle[j]) continue outer;
            return i;
        }
        return -1;
    }

    static ApprovalView view(JSONObject vectors, String risk) throws Exception {
        JSONObject challenge = vectors.getJSONObject("challenge");
        return new ApprovalView(new JSONObject().put("id", challenge.getString("id")).put("state", "pending").put("title", "claude-1a2b3c asks for 2 things").put("risk", risk)
                .put("session", challenge.getString("session")).put("text", challenge.getString("text")).put("expires_ms", challenge.getLong("expires_ms")).put("challenge", challenge));
    }

    static void theText(JSONObject vectors) throws Exception {
        ApprovalText parts = ApprovalText.parse(vectors.getJSONObject("challenge").getString("text"));
        check(parts.complete(), "the text has who, what, scope, what it cannot do, why, what a deny does and when it ends");
        equal(3, parts.flags.size(), "what looks off comes first and is kept");
        check(parts.said.startsWith("You said: \"add the Linux box to the pool\""), "what the person said");
        check(parts.verdict.contains("goes beyond"), "how the node judged it");
        check(parts.taint.contains("cannot vouch for"), "whether the session read content from outside");
        check(parts.held.contains("2026-09-21 14:28 UTC") && parts.pick.startsWith("After your fingerprint"), "the undo window and the number to match");
        check(parts.completeFor("grants") && parts.completeFor("hold"), "every part an agent's request carries is there");
        for (String label : new String[] {"SAID:", "VERDICT:", "TAINT:"}) {
            StringBuilder without = new StringBuilder();
            for (String line : vectors.getJSONObject("challenge").getString("text").split("\n")) if (!line.startsWith(label)) without.append(without.length() == 0 ? "" : "\n").append(line);
            check(!ApprovalText.parse(without.toString()).completeFor("grants") && ApprovalText.parse(without.toString()).completeFor("hold"), "a request an agent made without its " + label + " line is not complete");
        }
        check(parts.pool.startsWith("SECURE."), "the pool line says the policy");
        equal("sensitive", parts.level(), "the risk level");
        equal(2, parts.asks.size(), "two asks");
        check(!ApprovalText.parse("WHO: x\nsomething unlabelled").complete(), "a line that is not labelled is kept and the text is not complete");
        String routine = variant(vectors.getJSONObject("challenge"), "routine", false).getString("text");
        ApprovalText noted = ApprovalText.parse("NOTE: No grant you signed has ever named claude-1a2b3c.\nNOTE: This is the first request for sync_now on this device.\n" + routine);
        check(noted.complete() && noted.flags.isEmpty() && noted.notes.size() == 2, "a note is news the person may read, kept, and not a line the text cannot carry");
        check(!ApprovalFlow.opensScreen(noted.level(), !noted.flags.isEmpty()), "a routine request with only notes is answered from the notification");
    }

    /** The challenge with the text's flags kept or dropped and its RISK line naming {@code level}: what the node would have signed for such a request. */
    static JSONObject variant(JSONObject challenge, String level, boolean flagged) throws Exception {
        StringBuilder text = new StringBuilder();
        for (String line : challenge.getString("text").split("\n")) {
            if (line.startsWith("FLAG:") && !flagged) continue;
            String shown = line.startsWith("RISK: ") ? "RISK: " + level.toUpperCase(java.util.Locale.ROOT) + ". It is what it is." : line;
            text.append(text.length() == 0 ? "" : "\n").append(shown);
        }
        return new JSONObject(challenge.toString()).put("text", text.toString());
    }

    static void theNotification(JSONObject vectors) throws Exception {
        JSONObject vector = vectors.getJSONObject("challenge");
        Object[][] cases = {{"routine", false, false}, {"routine", true, true}, {"sensitive", false, true}, {"irreversible", true, true}, {"surprising", false, true}};
        for (Object[] one : cases) {
            String level = (String) one[0];
            boolean flagged = (Boolean) one[1];
            JSONObject challenge = variant(vector, level, flagged);
            // The unsigned field beside the challenge says routine every time: the text decides, never that.
            JSONObject raw = new JSONObject().put("v", 1).put("id", challenge.getString("id")).put("title", "claude-1a2b3c asks for 2 things").put("agent", "claude-1a2b3c").put("risk", "routine")
                    .put("expires_ms", challenge.getLong("expires_ms")).put("challenge", challenge);
            PushPayload payload = PushPayload.parse(raw.toString().getBytes(StandardCharsets.UTF_8));
            NotificationModel model = NotificationModel.of(payload, challenge.getLong("created_ms"));
            equal("Approval waiting from claude-1a2b3c", model.publicTitle, "the lock screen names only the agent");
            check(!model.publicText.contains("add_device") && !model.publicText.contains("lab") && !model.publicTitle.contains("lab"), "nothing else shows while locked");
            equal(challenge.getString("text"), model.body, "the expanded notification is the whole text that is signed");
            for (String label : new String[] {"POOL:", "RISK:", "SAID:", "WHO:", "VERDICT:", "TAINT:", "ASK 1:", "SCOPE 1:", "HELD:", "PICK:", "CANNOT:", "WHY:", "DENY:", "EXPIRES:"}) {
                check(model.body.contains(label), "the notification carries " + label);
            }
            equal(flagged, model.body.contains("FLAG:"), "flags are carried when the node wrote them");
            if (flagged) check(model.collapsed.startsWith("Check this one: "), "what looks off is first even when collapsed");
            equal((Boolean) one[2], model.approveOpensScreen, "only a routine request with nothing flagged is approved from the notification itself: " + level + " flagged " + flagged);
            equal(1800000L, model.timeoutMs, "the notification goes when the request ends");
            check(!model.quiet && model.labels[0].equals("Approve") && model.labels[1].equals("Deny") && model.labels[2].equals("Deny and block"), "a request an agent made offers approve, deny and deny and block");
        }
        JSONObject big = new JSONObject().put("v", 1).put("id", vector.getString("id")).put("title", "claude-1a2b3c asks for 8 things").put("agent", "claude-1a2b3c")
                .put("risk", "routine").put("expires_ms", vector.getLong("expires_ms")).put("more", true);
        NotificationModel more = NotificationModel.of(PushPayload.parse(big.toString().getBytes(StandardCharsets.UTF_8)), vector.getLong("created_ms"));
        check(more.approveOpensScreen && more.body.contains("Open to read the whole request."), "a request too large for a push is read on the screen first");
        refuses(() -> PushPayload.parse("{\"v\":2}".getBytes(StandardCharsets.UTF_8)), "a push of another version");
    }

    /** A node that answers what the checks need and records what it was sent. */
    static final class Recording implements Transport {
        final List<String> sent = new ArrayList<>();
        final List<String> unreachable = new ArrayList<>();
        String answer = "{\"ok\":true,\"result\":{\"state\":\"approved\"}}";
        int status = 200;

        @Override public byte[] request(String endpoint, String pin, String method, String path, String token, byte[] body) throws Exception {
            if (unreachable.contains(endpoint)) throw new java.io.IOException("no route");
            if (status == 401) throw new SecurityException("This phone connection expired or was removed. Set it up again.");
            sent.add(endpoint + " " + method + " " + path + " " + token + " " + new String(body, StandardCharsets.UTF_8));
            return answer.getBytes(StandardCharsets.UTF_8);
        }
    }

    /** The view of the request a challenge makes, as the node's list shows it. */
    static ApprovalView viewOf(JSONObject challenge) throws Exception {
        return new ApprovalView(new JSONObject().put("id", challenge.getString("id")).put("state", "pending").put("title", "claude-1a2b3c asks for 2 things")
                .put("session", challenge.getString("session")).put("text", challenge.getString("text")).put("expires_ms", challenge.getLong("expires_ms")).put("challenge", challenge));
    }

    interface Change { void apply(JSONObject context) throws Exception; }

    /** A copy of {@code challenge} whose context {@code change} has changed. */
    static JSONObject withContext(JSONObject challenge, Change change) throws Exception {
        JSONObject copy = new JSONObject(challenge.toString());
        change.apply(copy.getJSONObject("context"));
        return copy;
    }

    static void theDecision(JSONObject vectors) throws Exception {
        SoftwareAuthorizer key = new SoftwareAuthorizer();
        String device = vectors.getJSONObject("challenge").getString("node");
        long inside = vectors.getJSONObject("challenge").getLong("created_ms");
        Recording node = new Recording();
        NodeApi api = new NodeApi(node, "p".repeat(64), List.of("https://192.168.1.4:7449"));
        Decider decider = new Decider(api, key, device);
        ApprovalView view = viewOf(withContext(vectors.getJSONObject("challenge"), c -> c.put("repeat", 0).put("pick", new JSONArray())));
        equal("sensitive", view(vectors, "routine").risk, "the risk is the one the signed text names, never the unsigned field beside it");
        equal("a1b2 c3d4 e5f6 0718 293a 4b5c", Hex.words("a1b2c3d4e5f60718293a4b5c6d7e8f90"), "a key shows 24 characters in groups of four");
        equal("", Hex.words("a1b2"), "a short fingerprint shows nothing rather than a part");
        String[] outcome = new String[2];
        Decider.Done done = new Decider.Done() {
            @Override public void answered(String state) { outcome[0] = state; }
            @Override public void failed(String sentence) { outcome[1] = sentence; }
        };
        decider.decide(view, "approve", view.text, inside, done);
        equal("approved", outcome[0], "an approval is sent");
        equal(1, key.asked, "one fingerprint");
        JSONObject body = new JSONObject(node.sent.get(0).substring(node.sent.get(0).indexOf('{')));
        String fingerprint = Hex.sha256(key.publicPoint());
        check(key.verifies(view.challenge.bytes("approve", fingerprint), Hex.parse(body.getString("sig"))), "the node can verify what was sent");
        equal(Hex.sha256(view.text), body.getString("text_sha256"), "the digest of the text that was shown");
        equal("approve", body.getString("decision"), "the decision");
        check(node.sent.get(0).contains(" POST /phone/v1/decide  {"), "a decision carries no token: the signature is the proof");
        outcome[0] = outcome[1] = null;
        decider.decide(view, "approve", view.text + " ", inside, done);
        check(outcome[1] != null && key.asked == 1 && node.sent.size() == 1, "text that is not what was shown asks nobody and sends nothing");
        outcome[1] = null;
        decider.decide(view, "approve", view.text, view.expiresMs, done);
        check(outcome[1] != null && key.asked == 1 && node.sent.size() == 1, "a request that has ended asks nobody and sends nothing");
        outcome[1] = null;
        new Decider(api, key, Hex.sha256("another device")).decide(view, "approve", view.text, inside, done);
        check(outcome[1] != null && key.asked == 1 && node.sent.size() == 1, "a request for another device asks nobody and sends nothing");
        outcome[1] = null;
        key.refuses = "Nothing was signed: the fingerprint was not recognised.";
        decider.decide(view, "deny", view.text, inside, done);
        equal("Nothing was signed: the fingerprint was not recognised.", outcome[1], "a refused fingerprint is said in one sentence");
        check(node.sent.size() == 1, "and sends nothing");
        key.refuses = null;
        node.answer = "{\"ok\":false,\"error\":{\"code\":\"denied\",\"message\":\"That request was already approved, so it cannot be decided again.\"}}";
        outcome[1] = null;
        decider.decide(view, "approve", view.text, inside, done);
        equal("That request was already approved, so it cannot be decided again.", outcome[1], "the node's sentence reaches the person");
    }

    static void thePushHandler(JSONObject vectors) throws Exception {
        SoftwareAuthorizer pushKey = new SoftwareAuthorizer();
        byte[] auth = new byte[16];
        new SecureRandom().nextBytes(auth);
        JSONObject challenge = vectors.getJSONObject("challenge");
        byte[] plain = new JSONObject().put("v", 1).put("id", challenge.getString("id")).put("title", "claude-1a2b3c asks for 2 things").put("agent", "claude-1a2b3c")
                .put("risk", "sensitive").put("expires_ms", challenge.getLong("expires_ms")).put("challenge", challenge).toString().getBytes(StandardCharsets.UTF_8);
        List<byte[]> posted = new ArrayList<>();
        PushChannel channel = new PushChannel() {
            @Override public void register() { }
            @Override public void unregister() { }
        };
        check(channel != null, "a channel is only an interface");
        posted.add(WebPushCipher.encrypt(plain, pushKey.publicPoint(), auth, new SecureRandom()));
        for (byte[] body : posted) {
            check(indexOf(body, "claude-1a2b3c".getBytes(StandardCharsets.UTF_8)) < 0 && indexOf(body, "add_device".getBytes(StandardCharsets.UTF_8)) < 0, "the push channel carries ciphertext only");
        }
        List<NotificationModel> shown = new ArrayList<>();
        List<String> told = new ArrayList<>();
        PushHandler handler = new PushHandler(new PushHandler.Keys() {
            @Override public java.security.PrivateKey privateKey() { return pushKey.signing.getPrivate(); }
            @Override public byte[] point() { try { return pushKey.publicPoint(); } catch (Exception e) { throw new IllegalStateException(e); } }
            @Override public byte[] auth() { return auth; }
        }, (payload, model) -> shown.add(model), told::add, () -> challenge.getLong("created_ms"));
        handler.message(posted.get(0));
        equal(1, shown.size(), "a push for this phone is shown");
        equal(challenge.getString("text"), shown.get(0).body, "with the whole text");
        byte[] other = WebPushCipher.encrypt(plain, new SoftwareAuthorizer().publicPoint(), auth, new SecureRandom());
        handler.message(other);
        handler.message(new byte[] {1, 2, 3});
        equal(1, shown.size(), "a push this phone's key does not open shows nothing");
        handler.address("https://push.example.org/UPabc");
        equal(List.of("https://push.example.org/UPabc"), told, "the address a distributor gives is told to the device");
    }

    static void theNodeApi() throws Exception {
        Recording node = new Recording();
        node.unreachable.add("https://10.0.0.5:7449");
        NodeApi api = new NodeApi(node, "p".repeat(64), List.of("https://10.0.0.5:7449", "https://100.64.1.2:7449"));
        node.answer = "{\"ok\":true,\"result\":{\"approvals\":[],\"version\":3}}";
        equal(3, api.list("tok").getInt("version"), "a list");
        check(node.sent.get(0).startsWith("https://100.64.1.2:7449 POST /phone/v1/list tok "), "the next address answers when the first does not");
        api.list("tok");
        check(node.sent.get(1).startsWith("https://100.64.1.2:7449 "), "the address that answered is tried first next time");
        api.hello();
        check(node.sent.get(2).contains(" GET /phone/v1/hello  "), "hello is a GET with no token");
        node.answer = "{\"ok\":false,\"error\":{\"code\":\"denied\",\"message\":\"That code was not made for this device.\"}}";
        try {
            api.add(new JSONObject());
            throw new AssertionError("a refusal was taken for success");
        } catch (NodeError refused) {
            equal("That code was not made for this device.", refused.getMessage(), "the sentence of a refusal");
            equal("denied", refused.code, "and its code");
        }
        node.status = 401;
        try {
            api.list("old");
            throw new AssertionError("a removed phone read the list");
        } catch (SecurityException removed) {
            check(NodeApi.removed(removed), "a token the node does not know means this phone was removed");
        }
        Recording nobody = new Recording();
        nobody.unreachable.add("https://10.0.0.5:7449");
        try {
            new NodeApi(nobody, "p".repeat(64), List.of("https://10.0.0.5:7449")).list("tok");
            throw new AssertionError("an unreachable device answered");
        } catch (java.io.IOException unreachable) { }
    }

    /** A pick made at once by whoever the test is. */
    static Decider.Picker choosing(String number, List<List<String>> seen) {
        return (options, chosen, cancelled) -> {
            seen.add(options);
            if (number == null) cancelled.run(); else chosen.accept(number);
        };
    }

    static void theStepsAgainstBeingWornDown(JSONObject vectors) throws Exception {
        JSONObject vector = vectors.getJSONObject("challenge");
        SoftwareAuthorizer key = new SoftwareAuthorizer();
        String device = vector.getString("node");
        long inside = vector.getLong("created_ms");
        Recording node = new Recording();
        Decider decider = new Decider(new NodeApi(node, "p".repeat(64), List.of("https://192.168.1.4:7449")), key, device);
        ApprovalView view = viewOf(vector);
        equal(1L, view.challenge.repeat(), "the scope was declined once before");
        equal(List.of("12", "47", "83"), view.challenge.pick(), "the three numbers to choose among");
        String[] outcome = new String[2];
        Decider.Done done = new Decider.Done() {
            @Override public void answered(String state) { outcome[0] = state; }
            @Override public void failed(String sentence) { outcome[1] = sentence; }
        };
        List<List<String>> asked = new ArrayList<>();
        decider.decide(view, "approve", view.text, inside, choosing("47", asked), done);
        check(outcome[1] != null && outcome[1].contains("declined 1 time(s) before") && key.asked == 0 && node.sent.isEmpty(), "the plain approval of a scope declined before is refused and asks nobody");
        outcome[1] = null;
        decider.decide(view, "approve_repeat", view.text, inside, null, done);
        check(outcome[1] != null && key.asked == 0 && node.sent.isEmpty(), "a number to match with nobody to ask asks no fingerprint and sends nothing");
        outcome[1] = null;
        decider.decide(view, "approve_repeat", view.text, inside, choosing(null, asked), done);
        equal(1, key.asked, "the fingerprint comes first");
        check(outcome[1] != null && outcome[1].contains("no number was chosen") && node.sent.isEmpty(), "leaving the question sends nothing, and the request waits");
        decider.decide(view, "approve_repeat", view.text, inside, choosing("47", asked), done);
        equal("approved", outcome[0], "the deliberate approval is sent");
        equal(2, key.asked, "one fingerprint for each try, never two for one");
        equal(List.of("12", "47", "83"), asked.get(asked.size() - 1), "the person was offered the three numbers");
        JSONObject body = new JSONObject(node.sent.get(0).substring(node.sent.get(0).indexOf('{')));
        equal("47", body.getString("pick"), "the number chosen travels, unsigned");
        equal("approve_repeat", body.getString("decision"), "the decision is the deliberate one");
        check(key.verifies(view.challenge.bytes("approve_repeat", Hex.sha256(key.publicPoint())), Hex.parse(body.getString("sig"))), "and the signature is over it");
        check(key.lastStrong, "an approval of a request that is not routine asks for a strong fingerprint and nothing weaker");
        outcome[0] = null;
        decider.decide(view, "deny", view.text, inside, null, done);
        equal("approved", outcome[0], "a deny needs no number");
        JSONObject deny = new JSONObject(node.sent.get(1).substring(node.sent.get(1).indexOf('{')));
        check(!deny.has("pick") && deny.getString("decision").equals("deny"), "and carries none");
        decider.decide(view, "block", view.text, inside, null, done);
        JSONObject block = new JSONObject(node.sent.get(2).substring(node.sent.get(2).indexOf('{')));
        equal("block", block.getString("decision"), "deny and block is a decision of its own");
        check(key.verifies(view.challenge.bytes("block", Hex.sha256(key.publicPoint())), Hex.parse(block.getString("sig"))), "signed over its own bytes");
        ApprovalView noBlock = viewOf(withContext(vector, c -> c.put("block_ms", 0)));
        equal("", noBlock.labels()[2], "no block button when the text names no block");
        int sent = node.sent.size();
        decider.decide(noBlock, "block", noBlock.text, inside, null, done);
        check(node.sent.size() == sent && outcome[1].contains("does not offer"), "a block that is not offered is not signed");
        ApprovalView first = viewOf(withContext(vector, c -> c.put("repeat", 0)));
        decider.decide(first, "approve_repeat", first.text, inside, choosing("47", asked), done);
        check(node.sent.size() == sent && outcome[1].contains("not declined before"), "a scope nobody declined is approved the usual way");
    }

    static void theStrengthOfTheAnswer(JSONObject vectors) throws Exception {
        JSONObject vector = vectors.getJSONObject("challenge");
        long inside = vector.getLong("created_ms");
        SoftwareAuthorizer key = new SoftwareAuthorizer();
        Recording node = new Recording();
        Decider decider = new Decider(new NodeApi(node, "p".repeat(64), List.of("https://192.168.1.4:7449")), key, vector.getString("node"));
        String[] outcome = new String[2];
        Decider.Done done = new Decider.Done() {
            @Override public void answered(String state) { outcome[0] = state; }
            @Override public void failed(String sentence) { outcome[1] = sentence; }
        };
        ApprovalView routine = viewOf(withContext(variant(vector, "routine", false), c -> c.put("repeat", 0).put("pick", new JSONArray())));
        ApprovalView sensitive = viewOf(withContext(vector, c -> c.put("repeat", 0).put("pick", new JSONArray())));
        decider.decide(routine, "approve", routine.text, inside, done);
        check(outcome[0] != null && !key.lastStrong, "a routine request is answered with a fingerprint, a strong face or the PIN");
        outcome[0] = null;
        decider.decide(sensitive, "approve", sensitive.text, inside, done);
        check(outcome[0] != null && key.lastStrong, "a request above routine asks for a strong fingerprint or face and nothing weaker");
        key.strongSetUp = false;
        int asked = key.asked;
        outcome[0] = outcome[1] = null;
        decider.decide(sensitive, "approve", sensitive.text, inside, done);
        check(outcome[0] == null && outcome[1].contains("strong face unlock") && key.asked == asked, "a phone with only a PIN is not asked to approve what is not routine");
        outcome[0] = outcome[1] = null;
        decider.decide(sensitive, "deny", sensitive.text, inside, done);
        check(outcome[0] != null && !key.lastStrong, "a deny only takes authority away, so a PIN is enough for it");
        decider.decide(routine, "approve", routine.text, inside, done);
        check(outcome[0] != null, "but it approves a routine request as before");
    }

    static void theNoticesAndTheContext(JSONObject vectors) throws Exception {
        JSONObject vector = vectors.getJSONObject("challenge");
        String node = vector.getString("node");
        long now = vector.getLong("created_ms");
        new Challenge(vector).check(node, vector.getString("text"), now);
        for (String field : new String[] {"said", "verdict", "taint", "held", "prompted", "repeat", "pick", "block_ms"}) {
            JSONObject dropped = new JSONObject(vector.toString());
            dropped.getJSONObject("context").remove(field);
            refuses(() -> new Challenge(dropped).check(node, vector.getString("text"), now), "a challenge without " + field);
        }
        JSONObject noContext = new JSONObject(vector.toString());
        noContext.remove("context");
        refuses(() -> new Challenge(noContext).check(node, vector.getString("text"), now), "a challenge without a context");
        refuses(() -> new Challenge(withContext(vector, c -> c.put("extra", 1))).check(node, vector.getString("text"), now), "a context with a part the node never writes");
        refuses(() -> new Challenge(withContext(vector, c -> c.put("verdict", "fine"))).check(node, vector.getString("text"), now), "a verdict the node never writes");
        refuses(() -> new Challenge(withContext(vector, c -> c.put("pick", new JSONArray().put("1").put("2")))).check(node, vector.getString("text"), now), "two numbers to choose between");
        refuses(() -> new Challenge(withContext(vector, c -> c.getJSONObject("said").put("origin", "somewhere"))).check(node, vector.getString("text"), now), "an origin the node never writes");
        String key = vectors.getJSONObject("key").getString("fingerprint");
        byte[] original = new Challenge(vector).bytes("approve", key);
        Change[] changes = {c -> c.getJSONObject("said").put("quote", "add the Linux box"), c -> c.getJSONObject("said").put("origin", "app"), c -> c.put("verdict", "matches"),
                c -> c.put("taint", "clean"), c -> c.getJSONObject("held").put("act_at_ms", 1), c -> c.getJSONObject("held").put("cancel", false), c -> c.put("prompted", "no"),
                c -> c.put("repeat", 2), c -> c.put("pick", new JSONArray().put("1").put("2").put("3")), c -> c.put("block_ms", 1)};
        for (Change change : changes) {
            check(!java.util.Arrays.equals(original, new Challenge(withContext(vector, change)).bytes("approve", key)), "a changed part of the context changes the bytes that are signed");
        }
        JSONObject action = new JSONObject(vector.toString());
        action.getJSONObject("action").getJSONArray("grants").getJSONObject(0).put("resource", "lab");
        check(!java.util.Arrays.equals(original, new Challenge(action).bytes("approve", key)), "so does what it wants");
        equal(List.of("add_device on anything, once, for 10 minutes", "link_write on demo>lab, as often as it is needed, for 1 hour, each use reviewed"), Wants.of(new Challenge(vector)),
                "what it wants is read from the structured asks the key signs");
        equal("add_device on lab, once, for 10 minutes", Wants.of(new Challenge(action)).get(0), "and follows them when they change");
        check(Wants.of(new Challenge(new JSONObject(vector.toString()).put("action", new JSONObject().put("kind", "hold").put("cause", "flood")))).isEmpty(), "a notice asks for no grant");
        // A notice about requests kept back: its buttons ask something else, and a quiet one does not interrupt.
        for (String[] one : new String[][] {{"held", "Allow once", "Keep holding", "Block it"}, {"suspended", "Lift the suspension", "Keep it suspended", "Block it"}, {"flood", "Dismiss", "", ""}}) {
            JSONObject notice = new JSONObject(vector.toString());
            notice.put("action", new JSONObject().put("kind", "hold").put("cause", one[0]).put("key", "session:demo/claude-1a2b3c").put("queued", 3));
            ApprovalView view = viewOf(notice);
            equal(List.of(one[1], one[2], one[3]), java.util.Arrays.asList(view.labels()), "what the buttons of a " + one[0] + " notice say");
            JSONObject raw = new JSONObject().put("v", 1).put("id", notice.getString("id")).put("title", "a notice").put("agent", "").put("urgency", "quiet")
                    .put("expires_ms", notice.getLong("expires_ms")).put("challenge", notice);
            NotificationModel model = NotificationModel.of(PushPayload.parse(raw.toString().getBytes(StandardCharsets.UTF_8)), now);
            check(model.quiet && model.publicTitle.equals("A quiet notice is waiting") && model.approveOpensScreen, "a notice is quiet, says nothing at the lock screen and opens on the screen first");
        }
        ApprovalView flood = viewOf(new JSONObject(vector.toString()).put("action", new JSONObject().put("kind", "hold").put("cause", "flood")));
        SoftwareAuthorizer person = new SoftwareAuthorizer();
        Recording recording = new Recording();
        String[] told = new String[1];
        new Decider(new NodeApi(recording, "p".repeat(64), List.of("https://192.168.1.4:7449")), person, node).decide(flood, "deny", flood.text, now, new Decider.Done() {
            @Override public void answered(String state) { told[0] = "sent"; }
            @Override public void failed(String sentence) { told[0] = sentence; }
        });
        check(person.asked == 0 && recording.sent.isEmpty() && told[0].contains("does not offer"), "a notice that offers one answer is not signed with another");
        JSONObject loud = new JSONObject().put("v", 1).put("id", vector.getString("id")).put("title", "asks").put("agent", "claude-1a2b3c").put("urgency", "urgent")
                .put("expires_ms", vector.getLong("expires_ms")).put("challenge", vector);
        check(!NotificationModel.of(PushPayload.parse(loud.toString().getBytes(StandardCharsets.UTF_8)), now).quiet, "a request that interrupts is not quiet");
        JSONObject odd = new JSONObject(loud.toString()).put("urgency", "deafening");
        PushPayload strange = PushPayload.parse(odd.toString().getBytes(StandardCharsets.UTF_8));
        check(!NotificationModel.of(strange, now).quiet && strange.view.interrupts() && strange.urgency.equals("urgent"), "an urgency the node never writes is read as the usual one");
        JSONObject lacking = new JSONObject(vector.toString());
        StringBuilder kept = new StringBuilder();
        for (String line : vector.getString("text").split("\n")) if (!line.startsWith("SAID:")) kept.append(kept.length() == 0 ? "" : "\n").append(line);
        lacking.put("text", kept.toString());
        SoftwareAuthorizer nobody = new SoftwareAuthorizer();
        Recording sentNothing = new Recording();
        String[] answered = new String[1];
        new Decider(new NodeApi(sentNothing, "p".repeat(64), List.of("https://192.168.1.4:7449")), nobody, node).decide(viewOf(withContext(lacking, c -> c.put("repeat", 0).put("pick", new JSONArray()))), "deny", kept.toString(), now,
                new Decider.Done() {
                    @Override public void answered(String state) { answered[0] = "sent"; }
                    @Override public void failed(String sentence) { answered[0] = sentence; }
                });
        check(nobody.asked == 0 && sentNothing.sent.isEmpty() && answered[0].contains("leaves out something"), "a request an agent made without what the person said is not signed, and nobody is asked");
    }

    static void theFrictionBudget(JSONObject contract) {
        equal(contract.getInt("first_run"), ApprovalFlow.firstRun().size(), "the steps of the first run");
        equal(contract.getInt("approve_routine"), ApprovalFlow.approving("routine", false).size(), "the steps of a routine approval");
        equal(contract.getInt("approve_sensitive"), ApprovalFlow.approving("sensitive", false).size(), "the steps of a sensitive approval");
        equal(ApprovalFlow.approving("sensitive", false), ApprovalFlow.approving("irreversible", false), "an irreversible request takes the steps of a sensitive one");
        equal(List.of(ApprovalFlow.Step.TAP_NOTIFICATION, ApprovalFlow.Step.TAP_APPROVE, ApprovalFlow.Step.FINGERPRINT), ApprovalFlow.approving("routine", true), "a routine request the node flagged is read first and needs no pick");
        check(ApprovalFlow.approving("routine", false).size() == 2 && !ApprovalFlow.approving("routine", false).contains(ApprovalFlow.Step.PICK), "a routine request pays nothing for the defences against being worn down");
        equal(1L, ApprovalFlow.approving("sensitive", false).stream().filter(step -> step == ApprovalFlow.Step.PICK).count(), "one pick for what is not routine");
        equal(contract.getInt("approve_routine_locked"), ApprovalFlow.approving("routine", false, true).size(), "the steps of a routine approval from a locked phone");
        equal(contract.getInt("approve_sensitive_locked"), ApprovalFlow.approving("sensitive", false, true).size(), "the steps of a sensitive approval from a locked phone");
        equal(ApprovalFlow.Step.UNLOCK, ApprovalFlow.approving("routine", false, true).get(0), "the unlock comes first");
        equal(ApprovalFlow.approving("sensitive", false).size() + 1, ApprovalFlow.approving("sensitive", false, true).size(), "a locked phone costs exactly the unlock more");
        for (String risk : new String[] {"routine", "sensitive", "irreversible"}) {
            for (boolean flagged : new boolean[] {false, true}) {
                int prompts = 0;
                for (ApprovalFlow.Step step : ApprovalFlow.approving(risk, flagged)) if (step == ApprovalFlow.Step.FINGERPRINT) prompts++;
                equal(1, prompts, "one fingerprint to approve a " + risk + " request" + (flagged ? " that was flagged" : ""));
            }
        }
    }
}
