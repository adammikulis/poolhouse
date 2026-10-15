package app.poolhouse.phone;

import android.content.Context;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/** Governance requests checked by the same native rules as a pool device. */
final class NativeGovernance {
    static { System.loadLibrary("poolhouse_phone_governance"); }
    private NativeGovernance() { }

    private static native String call(String root, String operation, String payload);

    private static JSONObject invoke(String root, String operation, JSONObject payload) throws Exception {
        JSONObject result = new JSONObject(call(root, operation, payload.toString()));
        if (result.has("error")) throw new SecurityException(result.getString("error"));
        return result;
    }

    private static JSONObject binding(String anchor, String node, String tls, String publicKey) throws Exception {
        if (!anchor.matches("[0-9a-f]{64}") || !node.matches("[0-9a-f]{64}") || !tls.matches("[0-9a-f]{64}") || !publicKey.matches("04[0-9a-f]{128}")) throw new SecurityException("This pool key has no verified binding.");
        return new JSONObject().put("anchor", anchor).put("node", node).put("tls", tls).put("public", publicKey);
    }

    static void pinFounding(Context context, String handle, String tls, PoolKeyBinding.Request locallyVerified) throws Exception {
        String root = GovernanceCheckpoint.directory(context, handle).getPath();
        invoke(root, "pin", binding(locallyVerified.anchor, locallyVerified.deviceFingerprint, tls, locallyVerified.publicKey));
    }

    static void requireFoundingPin(Context context, String handle, String tls, PoolKeyBinding.Request locallyVerified) throws Exception {
        String root = GovernanceCheckpoint.directory(context, handle).getPath();
        invoke(root, "require-pin", binding(locallyVerified.anchor, locallyVerified.deviceFingerprint, tls, locallyVerified.publicKey));
    }

    static VerifiedRequest review(Context context, String handle, String anchor, String node, String tls, String publicKey,
                                  JSONObject proof, JSONObject request) throws Exception {
        String root = GovernanceCheckpoint.directory(context, handle).getPath();
        JSONObject bound = binding(anchor, node, tls, publicKey);
        JSONObject result = invoke(root, "review", new JSONObject().put("binding", bound).put("proof", proof).put("request", request));
        return new VerifiedRequest(root, handle, bound.toString(), result);
    }

    static final class VerifiedRequest implements AutoCloseable {
        private final String root, handle, binding, token, card, requestId;
        private final byte[] bytes;
        private final AtomicBoolean begun = new AtomicBoolean(), closed = new AtomicBoolean();

        private VerifiedRequest(String root, String handle, String binding, JSONObject verified) throws Exception {
            this.root = root;
            this.handle = handle;
            this.binding = binding;
            this.token = verified.getString("token");
            this.card = verified.getString("card");
            this.requestId = verified.getString("id");
            this.bytes = Hex.parse(verified.getString("bytes"));
        }

        String card() { return card; }
        String requestId() { return requestId; }

        byte[] begin(String localHandle, String anchor, String node, String tls, String publicKey) throws Exception {
            if (!handle.equals(localHandle) || !Canonical.of(new JSONObject(binding)).equals(Canonical.of(NativeGovernance.binding(anchor, node, tls, publicKey)))
                    || closed.get() || !begun.compareAndSet(false, true)) throw new SecurityException("This request is no longer bound to this pool key.");
            recheck();
            return bytes.clone();
        }

        void finish() throws Exception {
            if (!begun.get() || closed.get()) throw new SecurityException("This signing request is no longer active.");
            recheck();
            close();
        }

        private void recheck() throws Exception {
            invoke(root, "recheck", new JSONObject().put("token", token));
        }

        @Override public void close() {
            if (closed.compareAndSet(false, true)) {
                try { invoke(root, "cancel", new JSONObject().put("token", token)); }
                catch (Exception ignored) { }
            }
        }
    }
}
