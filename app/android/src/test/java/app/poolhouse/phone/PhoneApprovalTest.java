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
        equal(2, parts.flags.size(), "what looks off comes first and is kept");
        check(parts.pool.startsWith("SECURE."), "the pool line says the policy");
        equal("sensitive", parts.level(), "the risk level");
        equal(2, parts.asks.size(), "two asks");
        check(!ApprovalText.parse("WHO: x\nsomething unlabelled").complete(), "a line that is not labelled is kept and the text is not complete");
    }

    static void theNotification(JSONObject vectors) throws Exception {
        JSONObject challenge = vectors.getJSONObject("challenge");
        for (String risk : new String[] {"routine", "sensitive", "irreversible"}) {
            JSONObject raw = new JSONObject().put("v", 1).put("id", challenge.getString("id")).put("title", "claude-1a2b3c asks for 2 things").put("agent", "claude-1a2b3c").put("risk", risk)
                    .put("expires_ms", challenge.getLong("expires_ms")).put("challenge", challenge);
            PushPayload payload = PushPayload.parse(raw.toString().getBytes(StandardCharsets.UTF_8));
            NotificationModel model = NotificationModel.of(payload, challenge.getLong("created_ms"));
            equal("Approval waiting from claude-1a2b3c", model.publicTitle, "the lock screen names only the agent");
            check(!model.publicText.contains("add_device") && !model.publicText.contains("lab") && !model.publicTitle.contains("lab"), "nothing else shows while locked");
            equal(challenge.getString("text"), model.body, "the expanded notification is the whole text that is signed");
            for (String label : new String[] {"FLAG:", "POOL:", "RISK:", "WHO:", "ASK 1:", "SCOPE 1:", "CANNOT:", "WHY:", "DENY:", "EXPIRES:"}) {
                check(model.body.contains(label), "the notification carries " + label);
            }
            check(model.collapsed.startsWith("Check this one: "), "what looks off is first even when collapsed");
            equal(!risk.equals("routine"), model.approveOpensScreen, "only a routine request is approved from the notification itself");
            equal(1800000L, model.timeoutMs, "the notification goes when the request ends");
        }
        JSONObject big = new JSONObject().put("v", 1).put("id", challenge.getString("id")).put("title", "claude-1a2b3c asks for 8 things").put("agent", "claude-1a2b3c")
                .put("risk", "routine").put("expires_ms", challenge.getLong("expires_ms")).put("more", true);
        NotificationModel more = NotificationModel.of(PushPayload.parse(big.toString().getBytes(StandardCharsets.UTF_8)), challenge.getLong("created_ms"));
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

    static void theDecision(JSONObject vectors) throws Exception {
        SoftwareAuthorizer key = new SoftwareAuthorizer();
        String device = vectors.getJSONObject("challenge").getString("node");
        long inside = vectors.getJSONObject("challenge").getLong("created_ms");
        Recording node = new Recording();
        NodeApi api = new NodeApi(node, "p".repeat(64), List.of("https://192.168.1.4:7449"));
        Decider decider = new Decider(api, key, device);
        ApprovalView view = view(vectors, "sensitive");
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

    static void theFrictionBudget(JSONObject contract) {
        equal(contract.getInt("first_run"), ApprovalFlow.firstRun().size(), "the steps of the first run");
        equal(contract.getInt("approve_routine"), ApprovalFlow.approving("routine").size(), "the steps of a routine approval");
        equal(contract.getInt("approve_sensitive"), ApprovalFlow.approving("sensitive").size(), "the steps of a sensitive approval");
        equal(ApprovalFlow.approving("sensitive"), ApprovalFlow.approving("irreversible"), "an irreversible request takes the steps of a sensitive one");
        for (String risk : new String[] {"routine", "sensitive", "irreversible"}) {
            int prompts = 0;
            for (ApprovalFlow.Step step : ApprovalFlow.approving(risk)) if (step == ApprovalFlow.Step.FINGERPRINT) prompts++;
            equal(1, prompts, "one fingerprint to approve a " + risk + " request");
        }
    }
}
