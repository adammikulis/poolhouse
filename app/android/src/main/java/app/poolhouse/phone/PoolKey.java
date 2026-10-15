package app.poolhouse.phone;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.os.Build;
import android.os.CancellationSignal;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyInfo;
import android.security.keystore.KeyProperties;
import android.security.keystore.StrongBoxUnavailableException;
import java.security.KeyFactory;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/** Separate, nonexportable pool keys. No production transport uses this lifecycle yet. */
final class PoolKey {
    private static final Object LOCK = new Object();
    private static final Set<String> ACTIVE = new HashSet<>();
    private static final String PREFIX = "poolhouse-pool-";
    private static final int MAX_KEYS = 4;
    private final SharedPreferences bindings;
    private final String handle, device;

    private PoolKey(SharedPreferences bindings, String handle, String device) {
        this.bindings = bindings;
        this.handle = handle;
        this.device = device;
    }

    private static KeyStore store() throws Exception {
        KeyStore keys = KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        return keys;
    }

    /** Loading a bound key never creates or replaces it, even when enrollment has expired. */
    static PoolKey open(Context context, PhoneStore linked) throws Exception {
        final String node, device;
        synchronized (linked) {
            if (!linked.added()) throw unavailable();
            node = linked.text("pin");
            device = linked.device();
        }
        SharedPreferences saved = context.getSharedPreferences("poolhouse-pool-keys", Context.MODE_PRIVATE);
        synchronized (LOCK) {
            String handle = saved.getString("node_" + node, "");
            if (!node.matches("[0-9a-f]{64}") || !device.matches("[0-9a-f]{64}")
                    || !device.equals(saved.getString("device_" + handle, ""))
                    || !saved.getString("enrollment_" + handle, "").matches("[0-9a-f]{64}")) throw unavailable();
            PoolKey key = new PoolKey(saved, handle, device);
            key.checkedKey();
            return key;
        }
    }

    static final class EnrollmentProof {
        private final byte[][] chain;
        private final byte[] consent;
        private EnrollmentProof(byte[][] chain, byte[] consent) {
            this.chain = chain;
            this.consent = consent;
        }
        byte[][] chain() {
            byte[][] copy = new byte[chain.length][];
            for (int i = 0; i < chain.length; i++) copy[i] = chain[i].clone();
            return copy;
        }
        byte[] consent() { return consent.clone(); }
    }

    /** Evidence is reconstructed from this alias, never from a node-supplied replacement key. */
    EnrollmentProof enrollmentProof(PoolKeyEnrollment enrollment, long now) throws Exception {
        synchronized (LOCK) {
            enrollment.requireCurrent(now);
            if (!device.equals(enrollment.device) || !enrollment.binding().equals(bindings.getString("enrollment_" + handle, ""))
                    || !Hex.sha256(ApprovalKey.publicPoint()).equals(enrollment.approvalKey)) throw unavailable();
            checkedKey();
            java.security.cert.Certificate[] certificates = store().getCertificateChain(PoolKeyBinding.alias(handle));
            if (certificates == null || certificates.length < 2 || certificates.length > 8) throw unavailable();
            byte[][] chain = new byte[certificates.length][];
            java.io.ByteArrayOutputStream encoded = new java.io.ByteArrayOutputStream();
            java.io.DataOutputStream lengths = new java.io.DataOutputStream(encoded);
            int total = 0;
            for (int i = 0; i < certificates.length; i++) {
                chain[i] = certificates[i].getEncoded();
                total += chain[i].length;
                if (chain[i].length == 0 || chain[i].length > 16384 || total > 65536) throw unavailable();
                lengths.writeInt(chain[i].length);
                lengths.write(chain[i]);
            }
            byte[] point = publicPoint();
            if (!java.util.Arrays.equals(point, WebPushCipher.point(certificates[0].getPublicKey()))) throw unavailable();
            return new EnrollmentProof(chain, enrollment.consent(point, Hex.sha256(encoded.toByteArray())));
        }
    }

    /** A linked node's own TLS pin and device certificate fingerprint are distinct persisted values. */
    static PoolKey provision(Context context, PhoneStore linked, PoolKeyEnrollment enrollment) throws Exception {
        enrollment.requireCurrent(System.currentTimeMillis() / 1000);
        final String endpoint, device;
        synchronized (linked) {
            if (!linked.added()) throw unavailable();
            endpoint = linked.text("pin");
            device = linked.device();
        }
        if (!endpoint.matches("[0-9a-f]{64}") || !device.matches("[0-9a-f]{64}")
                || !endpoint.equals(enrollment.node) || !device.equals(enrollment.device)
                || !Hex.sha256(ApprovalKey.publicPoint()).equals(enrollment.approvalKey)) throw unavailable();
        SharedPreferences bindings = context.getSharedPreferences("poolhouse-pool-keys", Context.MODE_PRIVATE);
        synchronized (LOCK) {
            KeyStore keys = store();
            Set<String> known = new HashSet<>();
            boolean pending = false;
            for (String key : bindings.getAll().keySet()) {
                if (!key.startsWith("node_")) continue;
                String saved = bindings.getString(key, "");
                String alias = PoolKeyBinding.alias(saved);
                if (!known.add(alias) || !keys.containsAlias(alias)) throw unavailable();
                pending |= bindings.getString("anchor_" + saved, "").isEmpty();
            }
            java.util.Enumeration<String> aliases = keys.aliases();
            while (aliases.hasMoreElements()) {
                String alias = aliases.nextElement();
                if (alias.startsWith(PREFIX) && !known.contains(alias)) throw unavailable();
            }
            String slot = "node_" + endpoint;
            String handle = bindings.getString(slot, "");
            if (!handle.isEmpty()) {
                if (!device.equals(bindings.getString("device_" + handle, ""))
                        || !enrollment.binding().equals(bindings.getString("enrollment_" + handle, ""))) throw unavailable();
                PoolKey existing = new PoolKey(bindings, handle, device);
                existing.checkedKey();
                return existing;
            }
            if (pending || known.size() >= MAX_KEYS) throw unavailable();
            BiometricManager biometrics = context.getSystemService(BiometricManager.class);
            if (biometrics == null || biometrics.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) != BiometricManager.BIOMETRIC_SUCCESS) throw unavailable();
            byte[] random = new byte[16];
            new SecureRandom().nextBytes(random);
            handle = Hex.of(random);
            String alias = PoolKeyBinding.alias(handle);
            if (keys.containsAlias(alias)) throw unavailable();
            KeyPairGenerator generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
            boolean generated = false;
            if (context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE)) {
                try {
                    generator.initialize(spec(alias, enrollment).setIsStrongBoxBacked(true).build());
                    generator.generateKeyPair();
                    generated = true;
                } catch (StrongBoxUnavailableException unavailable) {
                    if (store().containsAlias(alias)) throw unavailable();
                }
            }
            if (!generated) {
                generator.initialize(spec(alias, enrollment).build());
                generator.generateKeyPair();
            }
            PoolKey made = new PoolKey(bindings, handle, device);
            made.checkedKey();
            if (!bindings.edit().putString(slot, handle).putString("device_" + handle, device).putString("enrollment_" + handle, enrollment.binding()).commit()) throw unavailable();
            return made;
        }
    }

    private static KeyGenParameterSpec.Builder spec(String alias, PoolKeyEnrollment enrollment) {
        return new KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setAttestationChallenge(enrollment.challenge())
                .setDigests(KeyProperties.DIGEST_SHA256).setUserAuthenticationRequired(true)
                .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
                .setInvalidatedByBiometricEnrollment(true);
    }

    private PrivateKey checkedKey() throws Exception {
        PrivateKey key = (PrivateKey) store().getKey(PoolKeyBinding.alias(handle), null);
        if (key == null || key.getEncoded() != null) throw unavailable();
        KeyInfo info = KeyFactory.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").getKeySpec(key, KeyInfo.class);
        if (!info.isUserAuthenticationRequired() || info.getUserAuthenticationType() != KeyProperties.AUTH_BIOMETRIC_STRONG
                || info.getUserAuthenticationValidityDurationSeconds() != -1 || !info.isInvalidatedByBiometricEnrollment()
                || !info.isUserAuthenticationRequirementEnforcedBySecureHardware() || !info.isInsideSecureHardware()) throw unavailable();
        if (Build.VERSION.SDK_INT >= 31 && info.getSecurityLevel() != KeyProperties.SECURITY_LEVEL_TRUSTED_ENVIRONMENT
                && info.getSecurityLevel() != KeyProperties.SECURITY_LEVEL_STRONGBOX) throw unavailable();
        return key;
    }

    byte[] publicPoint() throws Exception {
        synchronized (LOCK) {
            checkedKey();
            return WebPushCipher.point(store().getCertificate(PoolKeyBinding.alias(handle)).getPublicKey());
        }
    }

    /** Local diagnostic only: this is not attestation that a node can trust. */
    String hardwareLevel() throws Exception {
        synchronized (LOCK) {
            KeyInfo info = KeyFactory.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").getKeySpec(checkedKey(), KeyInfo.class);
            if (Build.VERSION.SDK_INT < 31) return "secure hardware";
            return info.getSecurityLevel() == KeyProperties.SECURITY_LEVEL_STRONGBOX ? "StrongBox" : "trusted environment";
        }
    }

    /** The future trusted full-card screen must display this immutable request before starting authentication. */
    PoolKeyBinding.Request review(JSONObject charter, String anchor, String pool, byte[] suppliedBytes) throws Exception {
        synchronized (LOCK) {
            return PoolKeyBinding.verify(charter, publicPoint(), device, anchor, pool, suppliedBytes,
                    bindings.getString("anchor_" + handle, ""));
        }
    }

    /** Signs only the reviewed request. No production caller or full-card screen is wired yet. Cancel on activity stop. */
    Operation sign(Activity activity, PoolKeyBinding.Request request, Authorizer.Result result) {
        Operation operation = null;
        try {
            synchronized (LOCK) {
                if (request == null || !request.deviceFingerprint.equals(device) || !request.publicKey.equals(Hex.of(publicPoint()))) throw unavailable();
                PoolKeyBinding.requireAnchor(bindings.getString("anchor_" + handle, ""), request.anchor);
                if (!ACTIVE.add(handle)) throw unavailable();
                try {
                    Signature signature = Signature.getInstance("SHA256withECDSA");
                    signature.initSign(checkedKey());
                    operation = new Operation(signature, request, result);
                } catch (Exception failure) {
                    ACTIVE.remove(handle);
                    throw failure;
                }
            }
            operation.show(activity);
        } catch (Exception failure) {
            if (operation != null) operation.cancel();
            else result.failed("This phone's pool key could not be used, so nothing was signed.");
            return null;
        }
        return operation;
    }

    final class Operation extends BiometricPrompt.AuthenticationCallback implements AutoCloseable {
        private final Signature signature;
        private final PoolKeyBinding.Request request;
        private final Authorizer.Result result;
        private final CancellationSignal cancellation = new CancellationSignal();
        private final AtomicBoolean done = new AtomicBoolean();
        private Operation(Signature signature, PoolKeyBinding.Request request, Authorizer.Result result) {
            this.signature = signature;
            this.request = request;
            this.result = result;
        }
        private void show(Activity activity) {
            BiometricPrompt prompt = new BiometricPrompt.Builder(activity).setTitle("Make this pool")
                    .setSubtitle("Use your fingerprint or strong face unlock")
                    .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG).setConfirmationRequired(true)
                    .setNegativeButton("Cancel", activity.getMainExecutor(), (dialog, which) -> cancel()).build();
            prompt.authenticate(new BiometricPrompt.CryptoObject(signature), cancellation, activity.getMainExecutor(), this);
        }
        @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult unlocked) {
            byte[] signed = null;
            synchronized (LOCK) {
                if (!done.compareAndSet(false, true)) return;
                try {
                    if (unlocked.getAuthenticationType() != BiometricPrompt.AUTHENTICATION_RESULT_TYPE_BIOMETRIC
                            || unlocked.getCryptoObject() == null || unlocked.getCryptoObject().getSignature() != signature) throw unavailable();
                    if (!request.publicKey.equals(Hex.of(publicPoint()))) throw unavailable();
                    String pinned = bindings.getString("anchor_" + handle, "");
                    PoolKeyBinding.requireAnchor(pinned, request.anchor);
                    if (pinned.isEmpty() && !bindings.edit().putString("anchor_" + handle, request.anchor).commit()) throw unavailable();
                    signature.update(request.bytes());
                    signed = P256Signature.normalize(signature.sign());
                } catch (Exception failure) {
                    // Preserve the key and binding; invalidation and persistence failures never regenerate either.
                } finally { ACTIVE.remove(handle); }
            }
            if (signed == null) result.failed("The pool could not be signed, so nothing was changed.");
            else result.signed(signed);
        }
        @Override public void onAuthenticationError(int error, CharSequence text) { cancel(); }
        void cancel() {
            synchronized (LOCK) {
                if (!done.compareAndSet(false, true)) return;
                ACTIVE.remove(handle);
            }
            cancellation.cancel();
            result.failed("Nothing was signed: the pool was not confirmed.");
        }
        @Override public void close() { cancel(); }
    }

    private static IllegalStateException unavailable() {
        return new IllegalStateException("This phone's pool key is unavailable.");
    }
}
